"""把需求节点渲染成**有预算上限**的紧凑规格。

为什么单独一层：旧管线把"描述 + 全部场景 + 全部可访问名"一次性拼进 design 请求，
实测全量需求下这一次请求 27k–121k 字符 → 六个 app 全部超预算（详见 .rebuild/LESSONS.md §5.1）。
这里的原则是**先算体积再拼**：每条规格都有上限，超了就按优先级裁，绝不生成一个送不出去的请求。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .loader import Node, Target, extract_targets, _quoted

# 单条需求规格的字符上限（描述 + 场景 + 靶子合计）。
PER_REQ_BUDGET = 1400


@dataclass
class ReqSpec:
    req_id: str
    title: str
    description: str
    scenarios: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    trimmed: bool = False

    def render(self) -> str:
        out = [f"### {self.req_id} · {self.title}"]
        if self.description:
            out.append(self.description.strip())
        if self.scenarios:
            out.append("**场景（外部测试会按它走一遍）**")
            out.extend(f"- {s}" for s in self.scenarios)
        if self.targets:
            out.append("**必须逐字出现的名字**（英文原文照抄，不许翻译；role = Playwright 会用的角色）")
            for t in self.targets:
                role = t.role or "(任意)"
                out.append(f"- [{role}] `{t.name}`")
        if self.images:
            out.append("参考图：" + ", ".join(self.images))
        if self.trimmed:
            out.append("（本条已按预算裁剪）")
        return "\n".join(out)

    def size(self) -> int:
        return len(self.render())


def build_spec(node: Node, *, budget: int = PER_REQ_BUDGET) -> ReqSpec:
    """单条需求 → 规格。超预算按"先丢场景细节、再丢描述尾部"的顺序裁。"""
    scen_lines: list[str] = []
    for sc in node.scenarios:
        for st in sc.steps:
            text = (st.content or "").strip()
            if text:
                scen_lines.append(f"{st.keyword}: {text}" if st.keyword else text)
    spec = ReqSpec(
        req_id=node.id,
        title=node.name,
        description=(node.description or "").strip(),
        scenarios=scen_lines,
        targets=extract_targets(node),
        images=list(node.images),
    )
    if spec.size() <= budget:
        return spec
    spec.trimmed = True
    # ① 先砍场景（它们是"过程描述"，靶子与描述才是契约）
    while spec.scenarios and spec.size() > budget:
        spec.scenarios.pop()
    # ② 再砍描述尾部
    while spec.description and spec.size() > budget:
        spec.description = spec.description[: max(120, len(spec.description) - 120)].rstrip()
    return spec


def render_block(specs: list[ReqSpec], header: str = "") -> str:
    parts = [header] if header else []
    parts.extend(s.render() for s in specs)
    return "\n\n".join(parts)


def sizes(specs: list[ReqSpec]) -> dict:
    total = sum(s.size() for s in specs)
    return {"count": len(specs), "total_chars": total,
            "max_chars": max((s.size() for s in specs), default=0),
            "trimmed": sum(1 for s in specs if s.trimmed)}