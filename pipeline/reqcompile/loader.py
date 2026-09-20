"""需求树加载 + 校验。

上游 ARC 的加载器（`core/files.py:11-23`）只做三件事：safe_load、
试着解包 root/requirement、要求 id 非空。**没有 schema 校验，没有查重，
没有依赖校验，没有环检测**（docs/03 §五）。

那份"宽松"在单节点示例上看不出代价，但在真实赛题上会静默吃掉节点：
重复 id 会让需求表用后者覆盖前者，同时给同一个 id 生成两条一模一样的
task_id；拼错的 dependency 一声不响地不生效。这些都不会报错，
只会让覆盖率悄悄少一块。

所以这里补上。这些都是纯代码、零 LLM 调用、零风险的收益。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .model import (
    RequirementNode,
    RequirementTree,
    Scenario,
    ScenarioStep,
    sha256_file,
)
from .yamlrepair import RepairResult, load_yaml_lenient

# description 里内嵌的 markdown 图片：![alt](./reference/home.png)
# 与 ARC 的抽取口径一致（core/visual_analysis.py:163）
RE_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

# 步骤关键字。代码同时接受 `type` 作为 `keyword` 的别名（pipeline.py:124-125）
STEP_KEYWORD_ALIASES = ("keyword", "type", "step_type")

# 解包用的包装键。ARC 依次尝试 root / requirement
WRAPPER_KEYS = ("root", "requirement")


class RequirementLoadError(ValueError):
    """加载失败。信息里带上文件与原因，别让调用方去猜。"""


@dataclass
class ValidationIssue:
    """一条校验发现。

    severity:
      error   —— 会让下游静默丢节点/丢边，必须修
      warning —— 可疑但不致命（例如某叶子没有场景 = 不会被评分覆盖）
    """

    severity: str
    code: str
    message: str
    node_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "node_id": self.node_id,
        }


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    # YAML 缩进修复留痕：我们改了输入，必须说得清改了哪里
    repairs: list[RepairResult] = field(default_factory=list)

    def add(self, severity: str, code: str, message: str, node_id: str | None = None) -> None:
        self.issues.append(ValidationIssue(severity, code, message, node_id))

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
            "yaml_repairs": [r.to_dict() for r in self.repairs],
        }


# ---------- 原始 YAML 处理 ----------

def _unwrap(raw: Any, source: Path) -> dict[str, Any]:
    """解包 root/requirement 包装，返回真正的根节点 dict。"""
    if not isinstance(raw, dict):
        raise RequirementLoadError(f"{source}: 顶层不是映射（是 {type(raw).__name__}）")

    for key in WRAPPER_KEYS:
        inner = raw.get(key)
        if isinstance(inner, dict):
            return inner

    # 没有包装就直接用；后面还要检查 id
    return raw


def _as_str_list(value: Any) -> list[str]:
    """dependencies / children 只接受字符串列表，其它一律当空。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return []


def _parse_steps(raw_steps: Any) -> list[ScenarioStep]:
    steps: list[ScenarioStep] = []
    if not isinstance(raw_steps, (list, tuple)):
        return steps
    for item in raw_steps:
        if isinstance(item, str):
            # 裸字符串步骤：没有关键字，按 WHEN 收着，别丢
            steps.append(ScenarioStep(keyword="WHEN", content=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        keyword = ""
        for alias in STEP_KEYWORD_ALIASES:
            candidate = item.get(alias)
            if isinstance(candidate, str) and candidate.strip():
                keyword = candidate.strip().upper()
                break
        content = item.get("content") or item.get("text") or item.get("description") or ""
        steps.append(ScenarioStep(keyword=keyword or "WHEN", content=str(content).strip()))
    return steps


def _parse_scenarios(raw_scenarios: Any, req_id: str) -> list[Scenario]:
    scenarios: list[Scenario] = []
    if not isinstance(raw_scenarios, (list, tuple)):
        return scenarios
    for item in raw_scenarios:
        if isinstance(item, str):
            scenarios.append(Scenario(name=item.strip(), steps=[], req_id=req_id))
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        scenario_id = item.get("id") or item.get("scenario_id")
        scenarios.append(
            Scenario(
                name=name,
                steps=_parse_steps(item.get("steps")),
                scenario_id=str(scenario_id).strip() if scenario_id else None,
                req_id=req_id,
            )
        )
    return scenarios


def _collect_visual_references(raw_node: dict[str, Any], description: str) -> list[str]:
    """收参考图，两种写法都支持（docs/03 §一）。

    注意：路径是**相对于 requirements.yaml 所在目录**解析的
    （`visual_analysis.py:57-59`），不是相对工作区。
    """
    refs: list[str] = []

    explicit = raw_node.get("visual_reference")
    if isinstance(explicit, (list, tuple)):
        for item in explicit:
            if isinstance(item, dict):
                p = item.get("image_path") or item.get("path")
                if p:
                    refs.append(str(p))
            elif isinstance(item, str):
                refs.append(item)
    elif isinstance(explicit, dict):
        p = explicit.get("image_path") or explicit.get("path")
        if p:
            refs.append(str(p))

    refs.extend(m.group(1) for m in RE_MD_IMAGE.finditer(description or ""))

    # 去重但保序
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        r = r.strip()
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _walk_node(
    raw: Any,
    tree: RequirementTree,
    report: ValidationReport,
    parent_id: str | None,
    seen_source_ids: dict[str, int],
    source: Path,
) -> str | None:
    """递归把一个 YAML 节点（及其 children）收进 tree。返回本节点 id。"""
    if not isinstance(raw, dict):
        report.add("error", "node_not_mapping",
                   f"{source}: children 里有一项不是映射（{type(raw).__name__}），已跳过")
        return None

    node_id = raw.get("id")
    if node_id is None or not str(node_id).strip():
        report.add("error", "node_missing_id", f"{source}: 有一个节点没有 id，已跳过")
        return None
    node_id = str(node_id).strip()

    # 查重：ARC 用 dict keyed by id，后者静默覆盖前者（docs/03 §五）
    seen_source_ids[node_id] = seen_source_ids.get(node_id, 0) + 1
    if seen_source_ids[node_id] > 1:
        report.add(
            "error", "duplicate_id",
            f"节点 id 重复：{node_id!r}（第 {seen_source_ids[node_id]} 次出现）。"
            "上游会静默覆盖前者，这里报出来。",
            node_id,
        )
        return node_id

    description = str(raw.get("description") or "")
    dependencies = _as_str_list(raw.get("dependencies"))
    children_raw = raw.get("children")
    scenarios = _parse_scenarios(raw.get("scenarios"), node_id)

    node = RequirementNode(
        id=node_id,
        name=str(raw.get("name") or "").strip(),
        type=str(raw.get("type") or "").strip(),
        description=description,
        dependencies=dependencies,
        children_ids=[],
        parent_id=parent_id,
        scenarios=scenarios,
        visual_references=_collect_visual_references(raw, description),
    )
    tree.nodes[node_id] = node

    if isinstance(children_raw, (list, tuple)):
        for child_raw in children_raw:
            child_id = _walk_node(child_raw, tree, report, node_id, seen_source_ids, source)
            if child_id:
                node.children_ids.append(child_id)

    return node_id


# ---------- 校验 ----------

def _detect_children_cycle(tree: RequirementTree, report: ValidationReport) -> None:
    """children 成环。

    YAML 递归锚点（`&a` / `*a`）能造出自引用——ARC 会直接 RecursionError
    （docs/03 §五）。我们没法靠 yaml.safe_load 拦住它（safe_load 要么
    已经炸了，要么就正常解析成嵌套结构），所以这里对建好的图再查一次。

    另外把"同一个 id 出现在两处 children"也点出来：那样它会被 walk
    访问两次，而 _walk_node 的查重会把第二次数成 duplicate。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in tree.nodes}
    stack: list[str] = []

    def visit(nid: str) -> bool:
        if color.get(nid) == GRAY:
            cycle = " → ".join(stack[stack.index(nid):] + [nid])
            report.add("error", "children_cycle", f"children 成环：{cycle}", nid)
            return True
        if color.get(nid) == BLACK:
            return False
        color[nid] = GRAY
        stack.append(nid)
        node = tree.nodes.get(nid)
        if node:
            for child in node.children_ids:
                if visit(child):
                    return True
        stack.pop()
        color[nid] = BLACK
        return False

    for nid in list(tree.nodes):
        if color.get(nid) == WHITE:
            if visit(nid):
                return


def _validate_dependencies(tree: RequirementTree, report: ValidationReport) -> None:
    """未知依赖 + 依赖环。

    ARC 两样都不查：未知 id 静默不生效，环要靠 YAML 锚点才会炸。
    """
    known = set(tree.nodes)

    for node in tree.ordered():
        for dep in node.dependencies:
            if dep not in known:
                report.add(
                    "error", "unknown_dependency",
                    f"节点 {node.id} 依赖了不存在的 id {dep!r}（上游会静默忽略）",
                    node.id,
                )
            if dep == node.id:
                report.add("error", "self_dependency", f"节点 {node.id} 依赖自己", node.id)

    # 依赖图找环（Kahn）：出不来拓扑序的就是环
    adj = {nid: [d for d in n.dependencies if d in known] for nid, n in tree.nodes.items()}
    indeg = {nid: 0 for nid in known}
    for src, deps in adj.items():
        for d in deps:
            indeg[d] += 1
    queue = [nid for nid, deg in indeg.items() if deg == 0]
    visited = 0
    while queue:
        cur = queue.pop()
        visited += 1
        for d in adj[cur]:
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    if visited != len(known):
        stuck = sorted(nid for nid, deg in indeg.items() if deg > 0)
        report.add("error", "dependency_cycle",
                   f"依赖成环，涉及 {len(stuck)} 个节点：{', '.join(stuck[:10])}"
                   + (" …" if len(stuck) > 10 else ""))


def _validate_coverage(tree: RequirementTree, report: ValidationReport) -> None:
    """覆盖性提示（warning 级）。

    外部测试是按 `scenarios[].name` 挂到叶子的（docs/02 §3）。
    叶子没有场景 = 不会被任何外部测试覆盖 = 白生成。
    这不是错误（需求里本来就可能有不评分的节点），但值得点名——
    它直接影响我们把 token 花在哪。
    """
    for node in tree.ordered():
        if node.is_leaf and not node.scenarios:
            report.add("warning", "leaf_without_scenarios",
                       f"叶子 {node.id} 没有 scenarios，不会被外部测试覆盖", node.id)
        if node.scenarios:
            for sc in node.scenarios:
                if not sc.name:
                    report.add("error", "scenario_without_name",
                               f"节点 {node.id} 有场景没有 name——"
                               "外部测试标题靠它对齐，空的等于无法挂测试", node.id)
                if not sc.steps:
                    report.add("warning", "scenario_without_steps",
                               f"节点 {node.id} 的场景 {sc.name!r} 没有步骤", node.id)


# ---------- 入口 ----------

def load_requirement_tree(path: str | Path) -> tuple[RequirementTree, ValidationReport]:
    """加载并校验需求树。

    path 可以是 requirements.yaml 本身，也可以是包含它的目录。
    """
    p = Path(path)
    if p.is_dir():
        # 两种真实存在的布局都要认：
        #   quickstart 这类：<dir>/requirements.yaml
        #   arc-bench 赛题： <app>/requirements/requirements.yaml
        candidates = [
            p / "requirements.yaml",
            p / "requirements.yml",
            p / "requirements" / "requirements.yaml",
            p / "requirements" / "requirements.yml",
        ]
        found = next((c for c in candidates if c.is_file()), None)
        if found is None:
            raise RequirementLoadError(
                f"{p}: 目录里找不到 requirements.yaml / .yml"
                "（找过它本身和它的 requirements/ 子目录）"
            )
        p = found
    if not p.is_file():
        raise RequirementLoadError(f"找不到需求文件：{p}")

    try:
        raw, repair_result = load_yaml_lenient(p)
    except ValueError as exc:
        raise RequirementLoadError(str(exc)) from exc

    root_dict = _unwrap(raw, p)
    root_id = root_dict.get("id")
    if root_id is None or not str(root_id).strip():
        raise RequirementLoadError(f"{p}: 根节点没有非空 id（ARC 同样要求）")
    root_id = str(root_id).strip()

    tree = RequirementTree(
        root_id=root_id,
        source_path=p,
        source_sha256=sha256_file(p),
    )
    report = ValidationReport()

    # 修过缩进就记一条醒目警告：树是修出来的，不是原件
    if repair_result.repairs:
        report.repairs.append(repair_result)
        detail = "；".join(
            f"第 {r.line} 行 {r.key!r} {r.old_indent}→{r.new_indent} 格"
            for r in repair_result.repairs
        )
        report.add(
            "warning", "yaml_indentation_repaired",
            f"需求文件不是合法 YAML，已修复 {len(repair_result.repairs)} 处缩进：{detail}。"
            "上游 ARC（yaml.safe_load）会在这种文件上直接抛 ScannerError。",
        )

    _walk_node(root_dict, tree, report, None, {}, p)

    _detect_children_cycle(tree, report)
    _validate_dependencies(tree, report)
    _validate_coverage(tree, report)

    return tree, report
