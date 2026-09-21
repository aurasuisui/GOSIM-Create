#!/usr/bin/env python3
"""从 `metrics.jsonl` 聚合成本表 —— **禁止手抄**。

## 存在理由

同类错数已经出现三次（`docs/10` 的 5 vs 6 次、`docs/12` §三 B 的 8 vs 9、§八 的 8 vs 10）。
最后一次的成因值得记住：**一次 run 里有两轮 selfcheck→repair**，
只抄前半段就得到中间态（131,360），真值是 156,343。
手抄一张表要跨几个文件、对几个数字，出错概率是 1；脚本聚合是 0。

## 用法

    python eval/aggregate_metrics.py runs/*-metrics.jsonl            # 逐文件汇总
    python eval/aggregate_metrics.py <file> --passed 3               # 多一列「token/通过条数」
    python eval/aggregate_metrics.py --markdown runs/*-metrics.jsonl # 直接出可粘贴的 markdown 表
    python eval/aggregate_metrics.py <file> --stages                 # 逐次调用明细（查中间态用）

退出码：给了 `--expect-total` 且对不上 → 1（用来做"数字变了要有人看一眼"的护栏）。
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys


def load(path: pathlib.Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001 —— 半行（进程被杀）跳过，但要报出来
            print(f"  ⚠️  {path.name}: 有一行不是合法 JSON（进程被杀留下的半行？）",
                  file=sys.stderr)
    return rows


def summarize(rows: list[dict]) -> dict:
    inp = sum((r.get("input_tokens") or 0) for r in rows)
    out = sum((r.get("output_tokens") or 0) for r in rows)
    rea = sum((r.get("reasoning_tokens") or 0) for r in rows)
    return {"calls": len(rows), "input": inp, "output": out, "reasoning": rea,
            "total": inp + out, "runs": sorted({r.get("run_id") or "?" for r in rows})}


def stage_rows(rows: list[dict]) -> list[tuple]:
    out = []
    for i, r in enumerate(sorted(rows, key=lambda r: (r.get("run_id") or "", r.get("call_index") or 0)), 1):
        out.append((r.get("call_index") or i, r.get("stage") or "?", r.get("model") or "?",
                    r.get("input_tokens") or 0, r.get("output_tokens") or 0,
                    r.get("reasoning_tokens") or 0, r.get("total_tokens") or 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="聚合 metrics.jsonl → 成本表（禁止手抄）")
    ap.add_argument("files", nargs="+", help="metrics.jsonl（支持通配，shell 展开或本脚本展开）")
    ap.add_argument("--passed", type=int, default=None, help="通过条数 → 多算一列 token/通过")
    ap.add_argument("--markdown", action="store_true", help="输出 markdown 表（可直接粘进文档）")
    ap.add_argument("--stages", action="store_true", help="逐次调用明细")
    ap.add_argument("--expect-total", type=int, default=None,
                    help="护栏：total 不等于它就退出码 1（数字变了要有人看一眼）")
    args = ap.parse_args()

    paths: list[pathlib.Path] = []
    for pat in args.files:
        hits = [pathlib.Path(p) for p in glob.glob(pat)]
        paths.extend(hits or [pathlib.Path(pat)])
    paths = [p for p in paths if p.is_file()]
    if not paths:
        print("没有找到任何 metrics 文件", file=sys.stderr)
        return 2

    all_rows: list[dict] = []
    if args.markdown:
        print("| 记录 | 调用 | input | output | reasoning（占输出） | total | 通过 | token/通过 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for p in paths:
        rows = load(p)
        if not rows:
            print(f"{p}: 空文件")
            continue
        s = summarize(rows)
        all_rows += rows
        pct = (s["reasoning"] / s["output"] * 100) if s["output"] else 0
        per = (s["total"] / args.passed) if args.passed else None
        if args.markdown:
            print(f"| `{p.name}` | {s['calls']} | {s['input']:,} | {s['output']:,} | "
                  f"{s['reasoning']:,}（{pct:.0f}%） | **{s['total']:,}** | "
                  f"{args.passed if args.passed else '—'} | "
                  f"{f'{per:,.0f}' if per else '—'} |")
        else:
            print(f"{p.name}")
            print(f"  调用 {s['calls']} 次 / run_id {', '.join(s['runs'])}")
            print(f"  input {s['input']:,}   output {s['output']:,}   "
                  f"reasoning {s['reasoning']:,}（占输出 {pct:.0f}%）")
            print(f"  total {s['total']:,}"
                  + (f"   |  token/通过 = {per:,.0f}（通过 {args.passed} 条）" if per else ""))
        if args.stages:
            print("  idx  stage                                        model              in      out  reason    total")
            for idx, stage, model, i, o, r, t in stage_rows(rows):
                print(f"  {idx:>3}  {stage[:44]:44s} {model[:16]:16s} {i:7,} {o:7,} {r:7,} {t:8,}")

    if len(paths) > 1 and not args.markdown:
        s = summarize(all_rows)
        print(f"合计：{len(paths)} 个文件 / {s['calls']} 次调用 / total {s['total']:,}")

    if args.expect_total is not None:
        got = summarize(all_rows)["total"]
        if got != args.expect_total:
            print(f"❌ 护栏未通过：total {got:,} ≠ 期望 {args.expect_total:,}", file=sys.stderr)
            return 1
        print(f"✅ 护栏通过：total {got:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
