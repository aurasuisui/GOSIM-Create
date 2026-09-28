"""红线自查：提交包里**不许出现题目特定字符串**（"不得预埋答案"）。

口径（AGENTS.md 红线 9）：**"全仓"读作"提交包"** —— 作用域就是这个 zip，
所以事实（app 名、样本名、实测数字）留在 `docs/` / `runs/` / `legacy/` 里没关系。

⚠️ 为什么必须是机器判据：这条红线**靠人眼防不住** —— 旧管线被打包脚本当场抓到 4 处，
而那 4 处全在"新写的注释里"。

用法：
  python g2/tools/check_bundle_strings.py                 # 扫 g2 的提交范围（app/ + templates/ + main.py）
  python g2/tools/check_bundle_strings.py --zip <zip>     # 扫一个已打好的包
退出码 0 = 干净；1 = 命中。
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "g2"

# 题目特定字符串：六个旧 app + 官方两个任务 + 需求 id 形态 + 样本里的专有名词。
PATTERNS: list[tuple[str, str]] = [
    (r"\b12306\b", "app 名 12306"),
    (r"\bbookstack\b", "app 名 bookstack"),
    (r"\bctrip\b", "app 名 ctrip"),
    (r"\bprestashop\b", "app 名 prestashop"),
    (r"\bstackoverflow\b", "app 名 stackoverflow"),
    (r"\bhackathon\b", "赛事名"),
    (r"\bshelf\b|\bShelf\b", "样本专有名词 Shelf"),
    (r"\bBookStack\b", "样本专有名词 BookStack"),
    (r"REQ-?\d+(\.\d+)*", "需求 id 字面量"),
    (r"\b[Tt]oggle sidebar\b", "样本 UI 名"),
    (r"\bQuickstart\b|ticketbooking", "样本名"),
]
ALLOW_SUFFIX = {".py", ".js", ".ts", ".tsx", ".json", ".md", ".txt", ".sql", ".css", ".html", ".yaml", ".yml"}


def scan_text(name: str, text: str) -> list[tuple[str, int, str, str]]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat, why in PATTERNS:
            m = re.search(pat, line)
            if m:
                hits.append((name, i, why, line.strip()[:120]))
    return hits


def files_in_scope() -> list[tuple[str, str]]:
    out = []
    for rel in ("main.py", "requirements.txt"):
        p = SRC / rel
        if p.is_file():
            out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
    for d in ("app", "templates"):
        base = SRC / d
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if not p.is_file() or p.suffix not in ALLOW_SUFFIX:
                continue
            if any(part in {"__pycache__", "node_modules"} for part in p.parts):
                continue
            out.append((p.relative_to(SRC).as_posix(), p.read_text(encoding="utf-8", errors="replace")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default="")
    args = ap.parse_args()
    if args.zip:
        with zipfile.ZipFile(args.zip) as z:
            items = [(n, z.read(n).decode("utf-8", "replace")) for n in z.namelist()
                     if not n.endswith("/") and Path(n).suffix in ALLOW_SUFFIX]
        scope = f"zip {args.zip}"
    else:
        items = files_in_scope()
        scope = "g2 提交范围（app/ + templates/ + main.py + requirements.txt）"
    hits: list[tuple[str, int, str, str]] = []
    for name, text in items:
        hits.extend(scan_text(name, text))
    print(f"扫描 {scope}：{len(items)} 个文本文件")
    if hits:
        for name, line, why, snippet in hits[:40]:
            print(f"  ❌ {name}:{line}  [{why}]  {snippet}")
        print(f"命中 {len(hits)} 处 → 红线 9 不通过")
        return 1
    print("✓ 0 处命中")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())