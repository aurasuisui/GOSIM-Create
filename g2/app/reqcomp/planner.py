"""分组与计划：把需求切成"能装进一次请求"的块，并派生要写的文件。

两条硬约束（都由旧管线的死法逼出来）：
  1. **任何一次请求都必须装得下**：分组前先算体积，不靠运行期异常兜底；
  2. **分组要跟着需求自身的结构走**（父节点 = 功能域），不要按 app 名硬编码——
     硬编码的分块器是旧管线 M2 卡住的直接原因（分块器写死指向注册应用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .loader import Node, Tree
from .spec import ReqSpec, build_spec

# 一次 design 调用的**需求部分**上限（不含契约与指令；总预算见 llm.REQUEST_BUDGET）。
DESIGN_CHUNK_CHARS = 11000


@dataclass
class Group:
    """一个功能域：一块需求 + 它要写的文件。"""
    key: str                      # 稳定标识（用父节点 id）
    label: str                    # 人读的名字
    specs: list[ReqSpec] = field(default_factory=list)

    def size(self) -> int:
        return sum(s.size() for s in self.specs)


def _domain_of(tree: Tree, node: Node) -> tuple[str, str]:
    """找这条需求所属的功能域 = 它最近的、有多个孩子的祖先。"""
    parent: dict[str, Node] = {}

    def walk(n: Node) -> None:
        for c in n.children:
            parent[c.id] = n
            walk(c)

    walk(tree.root)
    cur = node
    while cur.id in parent:
        p = parent[cur.id]
        if len(p.children) > 1 or p.id == tree.root.id:
            return (p.id, p.name)
        cur = p
    return (tree.root.id, tree.root.name)


def group_requirements(tree: Tree, *, chunk_chars: int = DESIGN_CHUNK_CHARS) -> list[Group]:
    """需求 → 若干"装得下"的分组。

    规则：先按功能域聚合；某个域太大就按顺序切成多个子组（**切点只在需求之间**，
    从不切一条需求内部——裁一条需求是 spec 层的事）。
    """
    domains: dict[str, Group] = {}
    order: list[str] = []
    for n in tree.scorable_leaves():
        key, label = _domain_of(tree, n)
        if key not in domains:
            domains[key] = Group(key=key, label=label)
            order.append(key)
        domains[key].specs.append(build_spec(n))

    # 域按需求出现顺序排好后，**贪心打包**：相邻域能装进一块就装一块。
    # 为什么不一块域一次调用：实测一个包能切出十几个功能域 → 十几次 design 调用（每次都要重发契约），
    # 既贵又慢；而合并后同一块里的需求往往共享页面（列表页 / 详情页常常同属一个域）。
    flat: list[tuple[str, str, ReqSpec]] = []
    for key in order:
        for s in domains[key].specs:
            flat.append((key, domains[key].label, s))
    groups: list[Group] = []
    cur = Group(key="", label="")
    cur_domains: list[str] = []
    for key, label, s in flat:
        if cur.specs and cur.size() + s.size() > chunk_chars:
            cur.key = cur.key or key
            groups.append(cur)
            cur = Group(key="", label="")
            cur_domains = []
        if not cur.specs:
            cur.key = key
            cur.label = label
        if key not in cur_domains:
            cur_domains.append(key)
        cur.specs.append(s)
    if cur.specs:
        cur.key = cur.key or "ROOT"
        groups.append(cur)
    for i, g in enumerate(groups, 1):
        if "#" not in g.key and len(groups) > 1:
            g.key = f"{g.key}#{i}"
        g.label = g.label + f"（第 {i} 块）" if len(groups) > 1 else g.label
    return groups


def plan_report(tree: Tree, groups: list[Group]) -> dict:
    return {
        "pack": tree.root.name,
        "requirements": len(tree.scorable_leaves()),
        "domains": len({g.key.split("#")[0] for g in groups}),
        "groups": [
            {"key": g.key, "label": g.label, "reqs": [s.req_id for s in g.specs],
             "chars": g.size(), "targets": sum(len(s.targets) for s in g.specs)}
            for g in groups
        ],
        "total_chars": sum(g.size() for g in groups),
    }