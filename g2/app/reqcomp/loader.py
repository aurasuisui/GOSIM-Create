"""g2 · 需求编译器（零 LLM）。

输入：赛题包目录（requirements.yaml + requirements.md [+ tests/]）
输出：一棵带 id/描述/场景/靶子的需求树。

为什么重写：旧实现把"整份 brief 塞进一次 design 请求"（实测 24k–121k 字符），
撞上自己的请求预算闸门 → 平台 run 在第一次调用就死。
新编译器的硬约束：**任何一次 LLM 请求的体积都由这里算得**。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# UI 名字在赛题里有三种包法：反引号 / 弯引号 / ASCII 双引号（实测同一份需求里会混用）。
QUOTE_PAIRS = [("`", "`"), ("“", "”"), ("‘", "’"), ('"', '"')]
RE_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

# 英文描述里的控件句式 → 角色。顺序有意义（长的先试）。
ROLE_PHRASES: list[tuple[str, str]] = [
    ("checkbox", "checkbox"),
    ("radio button", "radio"),
    ("password input", "textbox"),
    ("textbox", "textbox"),
    ("text box", "textbox"),
    ("input field", "textbox"),
    ("search box", "searchbox"),
    ("combobox", "combobox"),
    ("drop-down", "combobox"),
    ("dropdown", "combobox"),
    ("select", "combobox"),
    ("textarea", "textbox"),
    ("button", "button"),
    ("link", "link"),
    ("tab", "tab"),
    ("heading", "heading"),
    ("menu item", "menuitem"),
    ("form", "form"),
    ("field", "textbox"),
]


@dataclass
class Target:
    """一条 UI 靶子：可访问名 + 角色（角色空 = 任意可命中位置）。"""

    name: str
    role: str = ""
    source: str = ""
    req_id: str = ""
    is_regex: bool = False

    def key(self) -> tuple[str, str]:
        return (self.role, self.name)


@dataclass
class Step:
    keyword: str
    content: str


@dataclass
class Scenario:
    name: str
    steps: list[Step] = field(default_factory=list)


@dataclass
class Node:
    id: str
    name: str
    type: str
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def text(self) -> str:
        parts = [self.name, self.description or ""]
        for sc in self.scenarios:
            parts.append(sc.name)
            for st in sc.steps:
                parts.append(st.content)
        return "\n".join(p for p in parts if p)


@dataclass
class Tree:
    root: Node
    source_path: Path
    source_sha256: str
    repairs: list[str] = field(default_factory=list)

    def all_nodes(self) -> list[Node]:
        out: list[Node] = []

        def walk(n: Node) -> None:
            out.append(n)
            for c in n.children:
                walk(c)
        walk(self.root)
        return out

    def leaves(self) -> list[Node]:
        return [n for n in self.all_nodes() if n.is_leaf]

    def scorable_leaves(self) -> list[Node]:
        """有场景的叶子 = 会被外部测试覆盖的节点（与 ARC 口径一致）。"""
        return [n for n in self.leaves() if n.scenarios]


class LoadError(ValueError):
    pass


def _repair_yaml(text: str) -> tuple[Any, list[str]]:
    """解析器驱动的缩进修复（真实赛题里存在非法 YAML）。

    失败 → 取错误行号 → 从错误行附近真实出现过的缩进值里挑候选逐个试，
    只接受让错误位置前进（行号变大）或直接通过的改动；改不动就带诊断放弃。
    """
    repairs: list[str] = []
    lines = text.splitlines()
    for _attempt in range(12):
        try:
            return yaml.safe_load("\n".join(lines)), repairs
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            if mark is None:
                raise LoadError(f"YAML 解析失败且无位置信息：{exc}") from exc
            lineno = mark.line
            if lineno >= len(lines):
                raise LoadError(f"YAML 解析失败：{exc}") from exc
            cur = lines[lineno]
            stripped = cur.lstrip(" ")
            if not stripped:
                raise LoadError(f"YAML 解析失败：{exc}") from exc
            cur_indent = len(cur) - len(stripped)
            near = lines[max(0, lineno - 40):lineno + 40]
            cands = sorted({
                len(l) - len(l.lstrip(" "))
                for l in near
                if l.strip() and 0 <= len(l) - len(l.lstrip(" ")) < cur_indent
            }, reverse=True)
            progressed = False
            for cand in cands:
                lines[lineno] = " " * cand + stripped
                try:
                    yaml.safe_load("\n".join(lines))
                except yaml.YAMLError as exc2:
                    m2 = getattr(exc2, "problem_mark", None)
                    if m2 is not None and m2.line > lineno:
                        repairs.append(f"第 {lineno + 1} 行 缩进 {cur_indent}→{cand}")
                        progressed = True
                        break
                    lines[lineno] = cur
                    continue
                repairs.append(f"第 {lineno + 1} 行 缩进 {cur_indent}→{cand}")
                return yaml.safe_load("\n".join(lines)), repairs
            if not progressed:
                raise LoadError(f"YAML 解析失败且修不动：{exc}") from exc
    raise LoadError("YAML 修复超过 12 次仍未通过")


def _one_line(value: Any) -> str:
    """赛题 YAML 里的字符串被整体再包了一层引号（'"ROOT"'），要剥掉。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1].strip()
        if v[0] not in inner:
            return inner
    return v


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_node(raw: dict[str, Any]) -> Node:
    scens: list[Scenario] = []
    for sc in _as_list(raw.get("scenarios")):
        if not isinstance(sc, dict):
            continue
        steps: list[Step] = []
        for st in _as_list(sc.get("steps")):
            if not isinstance(st, dict):
                continue
            kw = _one_line(st.get("keyword") or st.get("type") or st.get("step_type") or "")
            steps.append(Step(keyword=kw, content=_one_line(st.get("content"))))
        scens.append(Scenario(name=_one_line(sc.get("name")), steps=steps))
    desc = _one_line(raw.get("description"))
    node = Node(
        id=_one_line(raw.get("id")),
        name=_one_line(raw.get("name")),
        type=_one_line(raw.get("type")) or ("FOLDER" if raw.get("children") else "ATOMIC"),
        description=desc,
        dependencies=[d for d in (_one_line(x) for x in _as_list(raw.get("dependencies"))) if d],
        scenarios=scens,
        images=RE_MD_IMAGE.findall(desc),
    )
    node.children = [_parse_node(c) for c in _as_list(raw.get("children")) if isinstance(c, dict)]
    return node


def find_requirements_file(pack_dir: Path) -> Path:
    cands = [
        pack_dir / "requirements.yaml",
        pack_dir / "requirements.yml",
        pack_dir / "requirements" / "requirements.yaml",
        pack_dir / "requirements" / "requirements.yml",
    ]
    for c in cands:
        if c.is_file():
            return c
    raise LoadError(f"{pack_dir} 里找不到 requirements.yaml（找过它本身与 requirements/ 子目录）")


def load_pack(pack_dir: str | Path) -> Tree:
    pack = Path(pack_dir)
    if pack.is_file():
        pack = pack.parent
    src = find_requirements_file(pack)
    text = src.read_text(encoding="utf-8")
    doc, repairs = _repair_yaml(text)
    if not isinstance(doc, dict):
        raise LoadError(f"{src}: 顶层不是映射")
    for wrapper in ("root", "requirement"):
        if wrapper in doc and isinstance(doc[wrapper], dict):
            doc = doc[wrapper]
            break
    root = _parse_node(doc)
    if not root.id:
        raise LoadError(f"{src}: 根节点没有 id")
    return Tree(root=root, source_path=src,
                source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                repairs=repairs)


def validate(tree: Tree) -> list[str]:
    """纯代码校验：重复 id / 未知依赖 / 依赖环 / 无场景叶子。"""
    issues: list[str] = []
    seen: dict[str, int] = {}
    for n in tree.all_nodes():
        if n.id in seen:
            issues.append(f"重复 id：{n.id}")
        seen[n.id] = seen.get(n.id, 0) + 1
    ids = set(seen)
    for n in tree.all_nodes():
        for d in n.dependencies:
            if d not in ids:
                issues.append(f"{n.id} 依赖了不存在的 {d}")
    graph = {n.id: [d for d in n.dependencies if d in ids] for n in tree.all_nodes()}
    state: dict[str, int] = {}

    def dfs(u: str, stack: list[str]) -> None:
        state[u] = 1
        for v in graph.get(u, []):
            if state.get(v) == 1:
                issues.append(f"依赖环：{u} → {v}（路径 {' → '.join(stack + [u, v])}）")
            elif state.get(v, 0) == 0:
                dfs(v, stack + [u])
        state[u] = 2

    for u in list(graph):
        if state.get(u, 0) == 0:
            dfs(u, [])
    no_scen = [n.id for n in tree.leaves() if not n.scenarios]
    if no_scen:
        issues.append("没有场景的叶子（不会被外部测试覆盖）：" + ", ".join(no_scen[:8]))
    return issues


ROLE_WORDS: dict[str, str] = {
    "button": "button", "buttons": "button",
    "link": "link", "links": "link",
    "textbox": "textbox", "textboxes": "textbox", "input": "textbox", "inputs": "textbox",
    "checkbox": "checkbox", "checkboxes": "checkbox",
    "radio": "radio", "radiogroup": "radio",
    "combobox": "combobox", "select": "combobox", "dropdown": "combobox",
    "tab": "tab", "tabs": "tab",
    "heading": "heading", "headings": "heading",
    "form": "form", "forms": "form",
    "table": "table", "cell": "cell", "grid": "grid",
    "menuitem": "menuitem", "menu": "menu",
    "searchbox": "searchbox", "dialog": "dialog", "alert": "alert",
}

RE_DECL_ROLE = re.compile(r"role\s*`([a-zA-Z]+)`\s*(?:named|called|名为)\s*`([^`]{1,60})`")
RE_DECL_CTRL = re.compile(r"\b(button|buttons|link|links|textbox|textboxes|input|inputs|checkbox|checkboxes"
                          r"|combobox|select|dropdown|tab|tabs|heading|headings|form|forms|menuitem"
                          r"|searchbox|dialog|alert|table|grid)\b[^`\n]{0,40}`([^`]{1,200})`", re.IGNORECASE)
RE_DECL_PLACEHOLDER = re.compile(r"placeholder\s*`([^`]{1,120})`")
RE_QUOTED = re.compile("`([^`]{1,80})`|“([^“”\\n]{1,80})”|‘([^‘’\\n]{1,80})’")
# 限定词形式：`exact placeholder `tag1, tag2`` / `labelled textbox `Name`` —— 限定词必须紧邻控件词
RE_DECL_QUALIFIED = re.compile(
    r"\b(exact\s+placeholder|placeholder|labelled|labeled|named)\s+(button|buttons|link|links|textbox|textboxes|input|inputs|checkbox|checkboxes|combobox"
    r"|select|dropdown|tab|tabs|heading|headings|form|forms|menuitem|searchbox|dialog|alert|table|grid)\b\s*`([^`]{1,120})`",
    re.IGNORECASE)
def _role_near(text: str, name: str) -> str:
    """在名字附近找控件词推角色；找不到返回空串（= 任意可命中位置）。"""
    idx = text.find(name)
    if idx < 0:
        return ""
    window = text[max(0, idx - 90): idx + len(name) + 90].lower()
    for phrase, role in ROLE_PHRASES:
        if phrase in window:
            return role
    return ""


def _quoted(text: str) -> list[str]:
    """取出所有被引号（反引号 / 弯引号 / 直角引号 / ASCII 双引号）包住的短语。

    为什么用**显式交替**而不是循环拼正则：反引号与双引号同名时，
    动态拼出来的字符类会把开引号也排除掉，导致匹配悄悄失败（踩过一次）。
    """
    out: list[str] = []
    for m in RE_QUOTED.finditer(text or ""):
        out.append(next(g for g in m.groups() if g is not None))
    return out


def extract_targets(node: Node) -> list[Target]:
    """抽出"外部测试会去找的名字"。全部零 token。

    赛题的写法是**声明式**的（实测）：
      role <X> named <Y>            → 角色 X，名字 Y
      button named <Y>              → 角色 button
      labelled textboxes <Y> and <Z>→ 角色 textbox
      exact placeholder <Y>         → 位置 = placeholder（不是普通 textbox）
    所以先按声明句式建 name→role 映射，再对所有引号名兜底用近邻推角色。
    """
    whole = node.text()
    declared: dict[str, str] = {}

    def remember(name: str, role: str) -> None:
        name = name.strip()
        if name and name not in declared:
            declared[name] = role

    # 优先级从高到低，**先到先得**：显式 role 声明 > 显式 placeholder > 通用控件词 > 近邻兜底。
    # 为什么：`reveals a textbox with exact placeholder `tag1, tag2`` 会被通用控件词错判成 textbox，
    # 而它真正的位置是 placeholder（同名不同位置的判据不同，见 verify 层）。
    for m in RE_DECL_ROLE.finditer(whole):
        role, name = m.group(1).strip(), m.group(2).strip()
        if role in ROLE_WORDS.values() or role in ROLE_WORDS:
            remember(name, ROLE_WORDS.get(role, role))
    for m in RE_DECL_PLACEHOLDER.finditer(whole):
        for name in _quoted(m.group(1) or ""):
            remember(name, "placeholder")
    for m in RE_DECL_CTRL.finditer(whole):
        word = m.group(1).lower()
        role = ROLE_WORDS.get(word) or ROLE_WORDS.get(word.rstrip("s")) or ""
        if not role:
            continue
        for name in _quoted(m.group(2) or ""):
            remember(name, role)
    for m in RE_DECL_QUALIFIED.finditer(whole):
        qualifier, role_word, name = m.group(1).lower(), m.group(2).lower(), m.group(3).strip()
        base = ROLE_WORDS.get(role_word) or ROLE_WORDS.get(role_word.rstrip("s"))
        if not base:
            continue
        remember(name, "placeholder" if "placeholder" in qualifier else base)

    out: list[Target] = []
    seen: set[tuple[str, str]] = set()

    def add(raw_name: str, source: str) -> None:
        name = raw_name.strip().strip(".,;:")
        if not name or len(name) > 60:
            return
        if name.lower() in {"true", "false", "yes", "no", "none", "null"}:
            return
        role = declared.get(name) or _role_near(whole, name)
        t = Target(name=name, role=role, source=source, req_id=node.id,
                   is_regex=bool(re.search(r"\.\*", name)))
        if t.key() in seen:
            return
        seen.add(t.key())
        out.append(t)

    for name in _quoted(node.description):
        add(name, "description")
    for sc in node.scenarios:
        for st in sc.steps:
            for name in _quoted(st.content):
                add(name, "step")
    return out
