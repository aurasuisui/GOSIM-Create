#!/usr/bin/env python3
"""把**逐条结果**（通过集合 / 失败集合）补进已有的 RunRecord。

存在理由：`per_test_passed` 是 2026-09-22 才加进 `extract_run.py` 的字段，
在那之前落盘的记录里它是 `None`——而 `docs/04` 的读数纪律写着
"拿旧记录做比较之前先确认字段有值，否则会拿『没记』当成『没通过』"。
补这一下零 token：`.judges.log` 还在盘上，重抽一次就有。

**只改这三个键**，其余字段（`model` / `change_description` / `load_snapshot` /
`git_commit` … 都是人工或环境注入的，日志里没有）一律原样保留：

    per_test_passed / per_test_failed（原本为空时）/ log_total_running

幂等：已有 `per_test_passed` 的记录不会被改（除非 `--force`）。

用法：
    python eval/backfill_per_test.py runs/*.json          # 只报"要不要补"
    python eval/backfill_per_test.py runs/*.json --write  # 真写
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_run import parse_log  # noqa: E402  （同一个解析器，口径必须一致）

ROOT = Path(__file__).resolve().parent.parent

# 要改的键（其余一律不动）
FILL_IF_EMPTY = ("per_test_failed",)
SET_ALWAYS = ("per_test_passed", "log_total_running")


def judges_log_for(record: dict) -> Path | None:
    """RunRecord 指向的是主日志；判据全文在**同名的** `.judges.log`。"""
    raw = record.get("log_path")
    if not raw:
        return None
    main = ROOT / raw
    cand = main.with_suffix(".judges.log")
    if cand.is_file():
        return cand
    # 兜底：主日志本身就是 playwright 输出（没有单独的 judges 文件）
    return main if main.is_file() else None


def backfill(path: Path, *, write: bool, force: bool) -> str:
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or "app" not in record:
        return "不是 RunRecord（runs/ 下也放别的 json），跳过"
    if record.get("per_test_passed") and not force:
        return "已有 per_test_passed，跳过"

    log = judges_log_for(record)
    if log is None:
        return "⚠️  找不到对应日志（log_path 指向的文件不在盘上——跨机器 clone 的已知后果）"

    parsed = parse_log(log.read_text(encoding="utf-8", errors="replace"))
    # 有汇总却抽不到逐条 → **不写**：写进去是 `[]`，等于把"没记"变成"记了是空"，
    # 正是这个工具要消掉的那个陷阱的反面。
    if (parsed.get("has_summary")
            and not parsed.get("per_test_passed") and not parsed.get("per_test_failed")):
        return "⚠️  日志有汇总行却抽不到逐条结果 → 不写（先修抽取器）"
    changes: list[str] = []
    for key in SET_ALWAYS:
        new = parsed.get(key)
        if record.get(key) != new:
            record[key] = new
            changes.append(key)
    for key in FILL_IF_EMPTY:
        if not record.get(key) and parsed.get(key):
            record[key] = parsed[key]
            changes.append(key)
    if not changes:
        return "无需改动"

    n_pass = len(record.get("per_test_passed") or [])
    n_fail = len(record.get("per_test_failed") or [])
    if write:
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return (f"{'✅ 已写' if write else '（dry-run）'} {', '.join(changes)}"
            f" → 通过 {n_pass} / 失败 {n_fail}"
            f"（源：{log.relative_to(ROOT)}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="补齐旧 RunRecord 的逐条结果")
    ap.add_argument("records", nargs="+")
    ap.add_argument("--write", action="store_true", help="真写（默认只报）")
    ap.add_argument("--force", action="store_true", help="已有 per_test_passed 也重写")
    args = ap.parse_args()
    for raw in args.records:
        p = Path(raw)
        if not p.is_absolute():
            p = ROOT / p
        if not p.is_file():
            print(f"{p.name}: 找不到")
            continue
        print(f"{p.name}: {backfill(p, write=args.write, force=args.force)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
