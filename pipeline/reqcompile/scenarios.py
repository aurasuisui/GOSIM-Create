"""场景索引 —— 生成阶段的靶子。

## 为什么这个索引是整条链上最要紧的东西

`docs/02-提交契约与打包.md` §3 记着外部测试的三条硬约束：

1. 每个 ATOMIC 需求**恰好一个** spec 文件；
2. spec 里的 `// requirement: REQ-x.y` 注释必须与文件名匹配；
3. **每个 `test()` 的标题必须与 `requirements.yaml` 里该节点的
   `scenarios[].name` 逐字、同序相等。**

再叠加禁用模式（不许 `page.goto`、不许直连 API、不许 class/id 选择器），
外部测试能验收的东西就只剩「点进去 + 按可访问名找到 + 断言可见文本」。
**`scenarios[].name` 是测试标题，`scenarios[].steps[].content` 是测试正文
唯一的语义来源。**

所以这个索引一旦错了，后面生成得再漂亮也挂不上测试。它必须是确定性的、
可核对的，并且在需求本身有歧义时**大声报出来**而不是悄悄猜。

## 上游漏掉的一环

ARC 只在 `scenario` 带 id 时才把它写进 scenarios 表
（`traceability.py:194-197`）——不带 id 的场景只留在 requirement 行的
内嵌数组里，**下游拿不到**。这里不丢：没有 id 就按
`{req_id}#{order}` 合成一个稳定的，保证每个场景都可寻址。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .model import RequirementNode, RequirementTree, Scenario


@dataclass
class ScenarioIndexEntry:
    """索引里的一行 = 外部测试里的一个 `test()`。"""

    req_id: str
    req_name: str
    order: int            # 在本节点内的序号（0-based）——测试必须同序
    name: str             # 逐字就是 test() 的标题
    scenario_id: str
    steps: list[dict[str, str]] = field(default_factory=list)
    is_leaf: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "req_id": self.req_id,
            "req_name": self.req_name,
            "order": self.order,
            "name": self.name,
            "scenario_id": self.scenario_id,
            "steps": list(self.steps),
            "is_leaf": self.is_leaf,
        }


@dataclass
class ScenarioIndex:
    entries: list[ScenarioIndexEntry] = field(default_factory=list)

    def by_req(self) -> dict[str, list[ScenarioIndexEntry]]:
        out: dict[str, list[ScenarioIndexEntry]] = defaultdict(list)
        for e in self.entries:
            out[e.req_id].append(e)
        for v in out.values():
            v.sort(key=lambda x: x.order)
        return dict(out)

    def by_name(self) -> dict[str, list[ScenarioIndexEntry]]:
        out: dict[str, list[ScenarioIndexEntry]] = defaultdict(list)
        for e in self.entries:
            out[e.name].append(e)
        return dict(out)

    @property
    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def duplicate_names(self) -> dict[str, int]:
        """标题重复的场景。

        逐个 `test()` 的标题必须唯一（Playwright 允许重名，但两个同名测试
        会让"哪一条失败了"变得说不清，也让覆盖率统计对不上）。
        """
        counts = Counter(e.name for e in self.entries if e.name)
        return {n: c for n, c in counts.items() if c > 1}

    def empty_names(self) -> list[ScenarioIndexEntry]:
        """没名字的场景 = 无法挂测试。"""
        return [e for e in self.entries if not e.name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_count": len(self.entries),
            "req_count": len({e.req_id for e in self.entries}),
            "entries": [e.to_dict() for e in self.entries],
            "duplicate_names": self.duplicate_names(),
        }


def _make_scenario_id(req_id: str, scenario: Scenario, order: int) -> str:
    """场景 id：有就用，没有就合成一个稳定的。

    合成规则用 `{req_id}#{order}`：同一份需求每次解析结果一致，
    所以它可以安全地写进 traceability。
    """
    if scenario.scenario_id and str(scenario.scenario_id).strip():
        return str(scenario.scenario_id).strip()
    return f"{req_id}#{order}"


def build_scenario_index(tree: RequirementTree) -> ScenarioIndex:
    """按树序（DFS 前序）建索引。顺序稳定，同序要求靠它满足。"""
    index = ScenarioIndex()
    for node in tree.ordered():
        for order, scenario in enumerate(node.scenarios):
            index.entries.append(
                ScenarioIndexEntry(
                    req_id=node.id,
                    req_name=node.name,
                    order=order,
                    name=scenario.name,
                    scenario_id=_make_scenario_id(node.id, scenario, order),
                    steps=[s.to_dict() for s in scenario.steps],
                    is_leaf=node.is_leaf,
                )
            )
    return index


def scenario_requirements(node: RequirementNode) -> list[str]:
    """本节点所有场景名（保持原序）—— 生成测试文件时的标题清单。"""
    return [s.name for s in node.scenarios]
