import sys
from pathlib import Path
sys.path.insert(0, "g2")
from app.reqcomp.loader import load_pack, _quoted
tree = load_pack(Path(sys.argv[1]))
nodes = tree.all_nodes()
steps = [st.content or "" for n in nodes for sc in (getattr(n, "scenarios", []) or []) for st in (sc.steps or [])]
desc = [(getattr(n, "description", "") or "") for n in nodes]
bt = chr(96)
print("steps:", len(steps), "| backticks in steps:", sum(s.count(bt) for s in steps))
print("descriptions:", len(desc), "| backticks in descriptions:", sum(d.count(bt) for d in desc))
joined = "\n".join(steps)
print("_quoted(steps):", _quoted(joined)[:8])
print("sample step:", repr(steps[0][:160]) if steps else "none")