import re, sys
from pathlib import Path
md = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
pats = [r"seeded data is account", r"seeded account", r"email `[^`]+`", r"password `[^`]+`"]
for p in pats:
    hits = re.findall(p, md, re.I)
    print(p, "->", len(hits))
print("--- 所有 seeded data is account 句子 ---")
for m in re.finditer(r"[^.]*seeded data is account[^.]*\.", md, re.I):
    print("  ", m.group(0).strip()[:220])
print("--- 出现的邮箱 ---")
emails = sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", md)))
print(" count:", len(emails))
for e in emails[:12]:
    print("   ", e)