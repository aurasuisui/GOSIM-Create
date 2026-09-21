#!/usr/bin/env python3
"""分块器验收（**不用生成**）—— `PLAN.md` §7 M3b-1 重订后明确要求的验收方式。

## 为什么验收不用生成

9/21 一天烧掉约 97 万 token，而三次跑（c/d/e）的失败**全部落在静态可判的规则上**。
"用生成去验静态规则"是那天最贵的教训。分块器产出的**分块计划本身是纯产物**，
所以先只跑**设计阶段（一次调用）**拿到计划，再做四项检查：

  ① **覆盖度**：design 里每条 route / 每个表 / 每条需求叶子都落进某一块
  ② **每块请求体积在预算内**（用**真实的** `_chunk_messages` 渲染出来量，不估算）
  ③ **一文件一调用**（M3a 实测：合并输出越大越容易被网关断连）
  ④ **每个文件恰好一个块**（两个块写同一个文件 = 后写的覆盖先写的）

成本是**几千 token**，不是几十万。

## 用法

    python eval/accept_chunkplan.py quickstart keep
    python eval/accept_chunkplan.py keep --req REQ-1,REQ-2      # 只放这几条需求进设计
    python eval/accept_chunkplan.py keep --design-file <path>   # 复用已有设计，0 token
    python eval/accept_chunkplan.py keep --a11y-only            # 只看可访问名靶子，0 token

退出码：全绿 0；有 ❌ 1。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT))

from generate.chunkplan import parse_design, plan_chunks          # noqa: E402
from generate.implement import (_chunk_messages, _design_prompt,   # noqa: E402
                               build_requirement_brief, build_skeleton_brief)
from generate.llm import MAX_REQUEST_CHARS, LLMConfig, chat        # noqa: E402
from reqcompile import extract_accessible_names, load_requirement_tree  # noqa: E402

SAMPLES = {
    "quickstart": ROOT / "repos" / "agentic-requirement-compiler" / "example" / "ticketbooking-quickstart",
    "keep": ROOT / "repos" / "arc-bench" / "arc-bench" / "webapp" / "keep",
}
for _name in ("12306", "bookstack", "ctrip", "prestashop", "stackoverflow"):
    SAMPLES[_name] = ROOT / "repos" / "arc-bench" / "arc-bench" / "webapp" / _name


def a11y_stats(tree) -> dict:
    """④ keep 的设计里有没有可访问名靶子（没有 → §4.1 第 5 条规则是它的前置）。"""
    a11y = extract_accessible_names(tree)
    leaves = [n for n in tree.ordered() if n.is_leaf and n.scenarios]
    by_req = a11y.by_req()
    covered = [n for n in leaves if by_req.get(n.id)]
    return {"entries": len(a11y.entries), "unique": len(a11y.unique_names()),
            "leaves": len(leaves), "leaves_with_names": len(covered),
            "coverage": (len(covered) / len(leaves)) if leaves else 0.0}


def run_sample(name: str, *, req_ids: list[str] | None, design_file: str | None,
               a11y_only: bool, log=print) -> int:
    path = SAMPLES.get(name)
    if path is None or not path.is_dir():
        print(f"❌ 样本不存在：{name} → {path}")
        return 1
    tree, _rep = load_requirement_tree(path)
    a11y = extract_accessible_names(tree)
    stats = a11y_stats(tree)
    print("=" * 78)
    print(f"样本 {name}   叶子(有场景) {stats['leaves']}   可访问名条目 {stats['entries']} "
          f"/ 去重 {stats['unique']}   叶子覆盖率 {stats['coverage']:.0%}")
    print("=" * 78)
    if stats["coverage"] < 0.5 or stats["entries"] == 0:
        print("🔴 **可访问名靶子不足** —— §4.1 第 5 条规则（散文名词枚举）是它的前置，先补它。")
        print("   （没有靶子 → 生成阶段不知道该做哪些 label → 判据会成片失配）")
    else:
        print("✅ 可访问名靶子够用（这条不是本次的阻塞项）")
    if a11y_only:
        return 0

    # ---- 设计：优先复用文件；否则真跑一次调用（几千 token）----
    if design_file:
        design_text = pathlib.Path(design_file).read_text(encoding="utf-8")
        print(f"设计来自文件：{design_file}（0 token）")
    else:
        brief = build_requirement_brief(tree, a11y, req_ids)
        print(f"设计输入 brief：{len(brief)} 字符")
        cfg = LLMConfig.from_env()
        cfg.require_key()
        print(f"发起**一次**设计调用（模型 {cfg.model}）…")
        design_text = chat(cfg, _design_prompt(brief), stage="design-accept", log=log)
        out = ROOT / "runs" / f"20260921T-accept-{name}-design.json"
        out.write_text(design_text, encoding="utf-8")
        print(f"设计已存 → {out}（下次可 --design-file 复用）")

    design = parse_design(design_text)
    if not design:
        print("❌ 设计不是合法 JSON —— 分块器无从派生（先看设计回复）")
        return 1
    print(f"设计键：{sorted(design)}")
    print(f"  routes {len(design.get('routes') or [])} / endpoints "
          f"{len(design.get('api_endpoints') or [])} / tables {len(design.get('db_tables') or [])}")

    chunks, report = plan_chunks(design, tree, a11y, req_ids=req_ids, log=log)

    # ---- ② 真实渲染体积 ③ 一文件一调用 ---- 
    skeleton = build_skeleton_brief(pathlib.Path(ROOT / "pipeline" / "templates"
                                                 / "web-react-express"), log=lambda *a, **k: None)
    print()
    print(f"{'块':30s} {'文件':>4s} {'需求':>4s} {'请求字符':>9s} {'占比':>6s}")
    print("-" * 78)
    fails: list[str] = []
    files_seen: list[str] = []
    for c in chunks:
        msgs = _chunk_messages(c, design_text, skeleton, [], "", log=lambda *a, **k: None)
        size = sum(len(m["content"]) for m in msgs)
        pct = size / MAX_REQUEST_CHARS
        flag = "✅" if size <= MAX_REQUEST_CHARS else "❌"
        if size > MAX_REQUEST_CHARS:
            fails.append(f"{c['name']} 请求 {size} 字符 > 预算 {MAX_REQUEST_CHARS}")
        if len(c["files"]) != 1:
            fails.append(f"{c['name']} 有 {len(c['files'])} 个文件（要求一文件一调用）")
        files_seen += c["files"]
        print(f"{c['name']:30s} {len(c['files']):4d} {len(c['nodes']):4d} {size:9,d} "
              f"{pct:5.0%} {flag}")

    dup = sorted({f for f in files_seen if files_seen.count(f) > 1})
    if dup:
        fails.append(f"这些文件被多个块占用（会互相覆盖）：{dup}")

    # ---- ① 覆盖度 ----
    routes = {str(r.get("path")) for r in (design.get("routes") or [])}
    tables = {str(t.get("name")) for t in (design.get("db_tables") or [])}
    asks = " ".join(c["ask"] for c in chunks).lower()
    briefs = " ".join(c["brief_slice"] for c in chunks).lower()
    for p in sorted(routes):
        if p and p != "/" and p.lower() not in asks:
            fails.append(f"路由 {p} 没出现在任何块的 ask 里")
    for t in sorted(tables):
        if t and t.lower() not in (asks + briefs):
            fails.append(f"表 {t} 没出现在任何块里")
    leaf_ids = {n.id for n in tree.ordered() if n.is_leaf and n.scenarios}
    if req_ids:
        leaf_ids &= set(req_ids)
    placed: set[str] = set()
    for c in chunks:
        placed |= {n.id for n in c["nodes"]}
    missing = sorted(leaf_ids - placed)
    if missing:
        fails.append(f"{len(missing)} 条需求没落进任何块：{missing[:6]}")

    print()
    print(f"路由 {len(routes)} / 表 {len(tables)} / 需求叶子 {len(leaf_ids)}"
          f" → 落位 {len(placed)}；块 {len(chunks)} 个；auth 块 {'有' if report['has_auth'] else '无'}")
    if fails:
        print(f"\n❌ 验收未过（{len(fails)} 项）：")
        for f in fails:
            print("   - " + f)
        return 1
    print("\n✅ 验收四项全过（覆盖度 / 体积 / 一文件一调用 / 文件不重复占用）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="分块器验收（不用生成）")
    ap.add_argument("samples", nargs="+", help=f"样本名：{', '.join(sorted(SAMPLES))}")
    ap.add_argument("--req", default=None, help="只放这几条需求进设计（逗号分隔）")
    ap.add_argument("--design-file", default=None, help="复用已有设计（0 token）")
    ap.add_argument("--a11y-only", action="store_true", help="只看可访问名靶子（0 token）")
    a = ap.parse_args()
    req_ids = [s for s in (a.req or "").split(",") if s.strip()] or None
    rc = 0
    for s in a.samples:
        rc |= run_sample(s, req_ids=req_ids, design_file=a.design_file, a11y_only=a.a11y_only)
    return rc


if __name__ == "__main__":
    sys.exit(main())
