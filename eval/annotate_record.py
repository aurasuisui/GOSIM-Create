#!/usr/bin/env python3
"""给**已有的** RunRecord 补 / 改字段（零 token，幂等，默认 dry-run）。

存在理由（第二十三轮审核 §三 A）：`r4-keep4b` 这份头条记录有两个护栏字段**读起来像"被污染"**——
`load_snapshot` 是持锁自采的人读句、`pipeline.git_dirty=true`——而解释只活在 `STATUS.md` 正文里。
按 `docs/04` §一 的机械口径筛，**这一轮会被丢掉**。修法是把解释**写进记录本身**，
但同时**不许把原始值抹掉**（那是取证材料）：

    · 校正后的值写进 `load_snapshot`
    · **原值**挪到 `load_snapshot_recorded_selfheld`（保留原样，便于复核）
    · 为什么改、凭什么改 → `snapshot_note`

用法：
    python eval/annotate_record.py runs/<record>.json \\
        --set change_description="…" --set load_snapshot="status=free runners=0 …" \\
        --set snapshot_note="…" [--write]

键支持一层点号路径（`pipeline.provenance_note=…`）。值先按 JSON 解析，
解析不了就当字符串（所以 `true` / `123` / `["a"]` 都会变成对应类型）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_value(raw: str):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001 —— 当字符串处理，这是常态
        return raw


def set_path(obj: dict, dotted: str, value) -> tuple[bool, object]:
    """设 `a.b` 形式的值；返回（是否变了, 旧值）。"""
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    old = cur.get(parts[-1])
    if old == value:
        return False, old
    cur[parts[-1]] = value
    return True, old


def main() -> int:
    ap = argparse.ArgumentParser(description="补/改已有 RunRecord 的字段")
    ap.add_argument("record")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--write", action="store_true", help="真写（默认只报会改什么）")
    args = ap.parse_args()

    p = Path(args.record)
    if not p.is_absolute():
        p = ROOT / p
    if not p.is_file():
        print(f"找不到：{p}", file=sys.stderr)
        return 1
    record = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        print("不是 RunRecord（顶层不是对象）", file=sys.stderr)
        return 1

    changes: list[tuple[str, object, object]] = []
    for item in args.set:
        if "=" not in item:
            print(f"⚠️  跳过（不是 KEY=VALUE）：{item}", file=sys.stderr)
            continue
        key, raw = item.split("=", 1)
        changed, old = set_path(record, key.strip(), parse_value(raw))
        changes.append((key.strip(), old, record.get(key.strip()) if "." not in key else None))
        marker = "✅" if changed else "＝"
        print(f"  {marker} {key.strip()}: {_short(old)} → {_short(parse_value(raw))}")

    if not args.write:
        print(f"\n（dry-run）以上是本工具**会**写进 {p.name} 的内容；加 --write 才落盘")
        return 0
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 已写 {p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}")
    return 0


def _short(v, n: int = 48) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "…"


if __name__ == "__main__":
    sys.exit(main())
