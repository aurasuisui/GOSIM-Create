"""需求散文的**契约覆盖率**实测（零 token）。

目的：官方题的判据是从需求正文生成的，而我没有官方测试可跑 —— 那就量"**正文里可验证的句子
有多少被我的抽取器抓进产物**"，用未覆盖率当"会丢分的代理指标"。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, "g2")
from app.reqcomp.aria import aria_contracts          # noqa: E402
from app.reqcomp.loader import extract_targets, load_pack  # noqa: E402

PATTERNS = [
    ("aria-role-prose", re.compile(r"(?:the\s+)?ARIA\s+([a-z][\w-]*)\s+role", re.I)),
    ("role-attr", re.compile(r"role\s*=\s*[\"\']([a-z][\w-]*)[\"\']", re.I)),
    ("accessible-name", re.compile(r"accessible name\s*[\"\u201c\']([^\"\u201d\']{1,40})", re.I)),
    ("aria-attr", re.compile(r"(aria-[\w-]+)\s*=\s*[\"\']([^\"\']{1,24})[\"\']", re.I)),
    ("quoted-name", re.compile(r"[\"\u201c]([A-Z][^\"\u201d]{2,40})[\"\u201d]")),
    ("element-role-phrase", re.compile(r"\b(button|link|tab|tablist|grid|gridcell|dialog|menu|menuitem|"
                                            r"checkbox|textbox|combobox|listbox|option|rowheader|columnheader|"
                                            r"heading|form|table|tree|treeitem|switch|slider|radiogroup)\b", re.I)),
]


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(".")
    for pack in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            tree = load_pack(pack)
        except Exception as exc:  # noqa: BLE001
            print(pack.name, "load failed:", type(exc).__name__)
            continue
        text = "\n".join([(getattr(n, "description", "") or "")
                          + "\n" + "\n".join(st.content or "" for sc in (getattr(n, "scenarios", []) or [])
                                              for st in (sc.steps or []))
                         for n in tree.all_nodes()])
        print("=" * 60)
        print(pack.name, "| chars:", len(text))
        covered = set()
        for n in tree.scorable_leaves():
            for t in extract_targets(n):
                covered.add(t.name.strip().lower())
        for n in tree.all_nodes():
            for t in [getattr(n, "description", "") or ""]:
                for c in aria_contracts(t):
                    covered.add(str(c.get("name") or "").strip().lower())
        for label, rx in PATTERNS:
            hits = rx.findall(text)
            flat = [h if isinstance(h, str) else " ".join(h) for h in hits]
            uniq = sorted({h.strip() for h in flat if str(h).strip()})
            miss = [u for u in uniq if u.lower() not in covered]
            print(f"  {label:20s} 出现 {len(hits):5d}  去重 {len(uniq):4d}  **未覆盖 {len(miss):4d}**")
            if miss and label in {"aria-role-prose", "accessible-name", "aria-attr", "role-attr"}:
                print("      未覆盖样例:", [m[:26] for m in miss[:8]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))