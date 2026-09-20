"""需求树的数据模型。

对应 docs/03-需求树与队列.md 里的 DSL。这里只放"解析后的形状"，
不放校验（校验在 loader.py）也不放抽取（在 scenarios/a11y/fixtures）。

一个刻意的决定：**不用 pydantic，用 dataclass。**
理由是我们的 bundle 要能被一个极薄的 requirements.txt 装起来，
而 pydantic 只在解析 YAML 时提供便利——那些便利我们自己写十几行就够了。
少一个重依赖 = 平台侧安装失败的面少一个。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------- 场景 ----------

@dataclass
class ScenarioStep:
    """GIVEN / WHEN / THEN 一条。"""

    keyword: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"keyword": self.keyword, "content": self.content}


@dataclass
class Scenario:
    """一个场景 = 外部测试里的一个 `test()`。

    这是整条链上最重要的一环：docs/02 §3 记着，外部测试的
    `test()` 标题必须与本节点的 `scenarios[].name` **逐字、同序**相等。
    所以 name 就是生成阶段的靶子，不能改写、不能翻译、不能加前缀。
    """

    name: str
    steps: list[ScenarioStep] = field(default_factory=list)
    scenario_id: str | None = None
    req_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "scenario_id": self.scenario_id,
            "req_id": self.req_id,
        }


# ---------- 节点 ----------

@dataclass
class RequirementNode:
    """需求树上的一个节点。"""

    id: str
    name: str = ""
    type: str = ""
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    children_ids: list[str] = field(default_factory=list)
    parent_id: str | None = None
    scenarios: list[Scenario] = field(default_factory=list)
    # description 内嵌 markdown 图片 + 显式 visual_reference 字段，两种都收
    visual_references: list[str] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        """叶子/非叶子只看有没有 children —— ARC 自己就是这么判的
        （docs/03 §一：全仓无任何 Python 代码读取 `type`）。"""
        return not self.children_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "dependencies": list(self.dependencies),
            "children_ids": list(self.children_ids),
            "parent_id": self.parent_id,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "visual_references": list(self.visual_references),
        }


@dataclass
class RequirementTree:
    """整棵需求树 + 源文件指纹。

    指纹是给实验记录用的：docs/04 要求每轮实验登记
    `requirements_sha256`，有了它才说得清"这次跑的是哪份需求"。
    """

    root_id: str
    nodes: dict[str, RequirementNode] = field(default_factory=dict)
    source_path: Path | None = None
    source_sha256: str = ""

    # 便捷索引，建一次省得反复遍历
    def leaves(self) -> list[RequirementNode]:
        return [n for n in self.ordered() if n.is_leaf]

    def leaves_with_scenarios(self) -> list[RequirementNode]:
        """有场景的叶子 —— 外部测试只挂在 ATOMIC 节点上，
        所以这批节点才是真正会被评分的那些。"""
        return [n for n in self.ordered() if n.is_leaf and n.scenarios]

    def ordered(self) -> list[RequirementNode]:
        """按 DFS 前序返回，顺序稳定（同一棵树每次跑都一样）。

        YAML 的书写顺序在这里被固定下来：ARC 的调度器"只取列表里
        第一个 PENDING"（docs/03 §二），也就是靠书写顺序当依赖序。
        我们显式保留这个顺序，避免以后换成按权重排序时踩自己。
        """
        out: list[RequirementNode] = []
        seen: set[str] = set()

        def walk(node_id: str) -> None:
            if node_id in seen:
                return
            node = self.nodes.get(node_id)
            if node is None:
                return
            seen.add(node_id)
            out.append(node)
            for child_id in node.children_ids:
                walk(child_id)

        walk(self.root_id)
        # 挂不上根的孤立节点也带出来，否则它们会静默消失
        for node_id in self.nodes:
            if node_id not in seen:
                walk(node_id)
        return out

    def all_scenarios(self) -> list[Scenario]:
        out: list[Scenario] = []
        for node in self.ordered():
            out.extend(node.scenarios)
        return out

    def to_nested_dict(self) -> dict[str, Any]:
        """转回 ARC 的**嵌套**形状，供 `store_requirement_tree` 消费。

        那个 SDK 方法是按 `children` 递归走的（`traceability.py:170-208`），
        不认我们扁平的 `children_ids`。

        🔑 这里补一个 ARC 的漏：`traceability.py:195-197` 只写带 id 的场景，
        **没有 id 的场景会被直接丢掉**——需求表里有、scenarios 表里没有，
        下游就寻址不到。我们给缺失的 id 合成 `{req_id}#{order}`，
        于是每个场景都能落表。
        """
        def build(node_id: str) -> dict[str, Any] | None:
            node = self.nodes.get(node_id)
            if node is None:
                return None
            return {
                "id": node.id,
                "name": node.name,
                "type": node.type,
                "description": node.description,
                "dependencies": list(node.dependencies),
                "visual_reference": list(node.visual_references),
                "scenarios": [
                    {
                        # 无 id 的补一个稳定的合成 id，别让 SDK 把它丢掉
                        "id": sc.scenario_id or f"{node.id}#{order}",
                        "name": sc.name,
                        "steps": [s.to_dict() for s in sc.steps],
                    }
                    for order, sc in enumerate(node.scenarios)
                ],
                "children": [
                    child for child in (build(cid) for cid in node.children_ids)
                    if child is not None
                ],
            }

        return build(self.root_id) or {}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
