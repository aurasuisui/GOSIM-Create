import json, re, sys
from pathlib import Path
sys.path.insert(0, "g2")
from app.reqcomp.loader import load_pack
pack = Path(sys.argv[1])
nav = json.loads((Path(sys.argv[2]) / ".arc" / "nav_targets.json").read_text(encoding="utf-8"))
nav_names = {str(x.get("name") or "").strip().lower() for x in nav}
tree = load_pack(pack)
text = "\n".join([(getattr(n, "description", "") or "")
                  + "\n" + "\n".join(st.content or "" for sc in (getattr(n, "scenarios", []) or [])
                                      for st in (sc.steps or []))
                 for n in tree.all_nodes()])
rx = re.compile(r"[\"\u201c\u2018]([A-Z][^\"\u201d\u2019\n]{2,44})[\"\u201d\u2019]")
names = sorted({m.strip() for m in rx.findall(text)})
miss = [n for n in names if n.lower() not in nav_names]
print("quoted names:", len(names), "| in nav_targets:", len(names) - len(miss), "| MISSING:", len(miss))
for m in miss[:22]:
    print("   -", m[:46])