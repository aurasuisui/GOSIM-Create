import re, sys
from pathlib import Path
sys.path.insert(0, "g2")
from app.reqcomp.loader import load_pack, _quoted
tree = load_pack(Path(sys.argv[1]))
texts = []
for n in tree.all_nodes():
    texts.append(getattr(n, "description", "") or "")
    for sc in (getattr(n, "scenarios", []) or []):
        for st in (sc.steps or []):
            texts.append(st.content or "")
hits = [t for t in texts if re.search(r"\bseed\b|seeded|contains", t, re.I)]
print("sentences mentioning seed:", len(hits))
for h in hits[:4]:
    print("  ", h[:170].replace(chr(10), " "))
lits = []
for h in hits:
    lits.extend(_quoted(h))
print("quoted literals in them:", len(lits), lits[:12])