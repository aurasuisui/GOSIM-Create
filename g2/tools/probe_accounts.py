import re, sys
from pathlib import Path
sys.path.insert(0, "g2")
from app.reqcomp.loader import load_pack
from app.reqcomp.contract import seeds_from_seed_sentences, seeds_from_requirements

tree = load_pack(Path(sys.argv[1]))
texts = []
for n in tree.all_nodes():
    texts.append((getattr(n, "description", "") or ""))
    for sc in (getattr(n, "scenarios", []) or []):
        for st in (sc.steps or []):
            texts.append(st.content or "")
joined = "\n".join(texts)
print("--- 需求里出现账号/口令的句子（前 6 条）---")
hits = [t for t in texts if re.search(r"account|password|sign in|credential", t, re.I)][:6]
for h in hits:
    print("  ", h[:170].replace(chr(10), " "))
print()
canon = seeds_from_seed_sentences(tree)
other = seeds_from_requirements(tree)
print("canonical seeds:", len(canon), [ (r["table"], list(r["values"].values())[:1]) for r in canon[:6] ])
users = [r for r in canon + other if r["table"] == "users"]
print("users rows:", len(users), users[:6])
emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", joined))
print("文中邮箱:", len(emails), sorted(emails)[:8])