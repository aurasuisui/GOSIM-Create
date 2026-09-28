import sys, json
from pathlib import Path
sys.path.insert(0, "g2")
from app.reqcomp.loader import load_pack
from app.gen.design import Blueprint
from app.gen.trace import write_traceability
out = Path("g2/work/plat-github")
bp_data = json.loads((out / ".arc" / "g2-blueprint.json").read_text(encoding="utf-8"))
bp = Blueprint(**{k: v for k, v in bp_data.items() if k in {"routes","api_endpoints","db_tables","auth","error_shape","notes"}})
tree = load_pack(Path(sys.argv[1]) if len(sys.argv) > 1 else "g2/packs/bookstack")
stats = write_traceability(out, tree, bp, log=print)
print("STATS:", json.dumps(stats, ensure_ascii=False))
root = out / ".arc" / "traceability"
for f in sorted(root.glob("*.json")):
    data = json.loads(f.read_text(encoding="utf-8"))
    print("  ", f.name, len(data))
ev = out / ".arc" / "runner-events.jsonl"
print("events lines:", len(ev.read_text(encoding="utf-8").splitlines()) if ev.is_file() else 0)