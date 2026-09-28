"""零 token 静态体检：需求包 → 靶子清单。

用法：python g2/tools/pack_report.py [pack_dir ...]
输出：每个包的节点/场景/靶子统计 + 每个需求的靶子清单（前若干条）。
这是重写后的第一条纪律工具：**任何进入 prompt 的东西，体积与内容都先在这里看得见**。
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.reqcomp.loader import extract_targets, load_pack, validate  # noqa: E402


def report(pack_dir: Path) -> None:
    tree = load_pack(pack_dir)
    leaves = tree.scorable_leaves()
    print("=" * 78)
    print(f"{pack_dir}  root={tree.root.id} {tree.root.name!r}")
    print(f"  节点 {len(tree.all_nodes())} / 叶子 {len(tree.leaves())} / 有场景叶子 {len(leaves)}"
          f"  sha256={tree.source_sha256[:12]}")
    if tree.repairs:
        print(f"  ⚠️ YAML 修复 {len(tree.repairs)} 处：" + "; ".join(tree.repairs))
    issues = validate(tree)
    for i in issues:
        print(f"  ⚠️ {i}")

    per_req: dict[str, list] = {}
    for n in leaves:
        per_req[n.id] = extract_targets(n)
    flat = [t for v in per_req.values() for t in v]
    roles = collections.Counter(t.role or "(none)" for t in flat)
    print(f"  靶子 {len(flat)} 条，角色分布 {dict(roles)}")
    noreq = [rid for rid, v in per_req.items() if not v]
    if noreq:
        print(f"  ⚠️ 抽不到任何靶子的需求 {len(noreq)} 个：{', '.join(noreq[:10])}")
    # 场景文本体量（决定 prompt 预算）
    sizes = []
    for n in leaves:
        body = n.description or ""
        scen = " ".join(st.content for sc in n.scenarios for st in sc.steps)
        sizes.append((n.id, len(body), len(scen)))
    print(f"  描述合计 {sum(s[1] for s in sizes)} 字符 / 场景合计 {sum(s[2] for s in sizes)} 字符")
    heavy = sorted(sizes, key=lambda x: -(x[1] + x[2]))[:3]
    print("  最重的三条需求（描述+场景）：" + ", ".join(f"{i}({d}+{s})" for i, d, s in heavy))

    print("  --- 前 6 个需求的靶子 ---")
    for n in leaves[:6]:
        tg = per_req[n.id]
        print(f"   {n.id:<12} {n.name[:34]:<34} {len(tg):>2} 条: "
              + "; ".join(f"[{t.role or '*'}] {t.name}" for t in tg[:6]))


def main(argv: list[str]) -> int:
    packs = [Path(a) for a in argv[1:]] or [Path("g2/packs/bookstack"), Path("g2/packs/keep")]
    for p in packs:
        report(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))