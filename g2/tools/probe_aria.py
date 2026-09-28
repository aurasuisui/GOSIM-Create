import sys, json
from pathlib import Path
sys.path.insert(0, "g2")
from app.reqcomp.aria import aria_contracts, example_cells
from app.reqcomp.loader import load_pack
for name in ("hackathon--sheet", "hackathon--github"):
    p = Path(sys.argv[1]) / name if len(sys.argv) > 1 else None
    if p is None or not p.is_dir():
        continue
    tree = load_pack(p)
    found = []
    for n in tree.all_nodes():
        texts = [n.description or ""] + [st.content or "" for sc in n.scenarios for st in sc.steps]
        for t in texts:
            found.extend(aria_contracts(t))
    uniq = {(c["role"], c["name"]) for c in found}
    print(name, "-> ARIA contracts:", len(found), "unique:", len(uniq))
    for c in list(uniq)[:10]:
        print("   ", c)