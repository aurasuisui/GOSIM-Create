import json, re, sys
from pathlib import Path
sys.path.insert(0, "g2")
from app.reqcomp.loader import load_pack, _quoted
from app.reqcomp.contract import nav_from_requirements, requirement_literals

d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig", errors="replace"))
md = d.get("requirements_markdown") or ""
tree = load_pack(Path(sys.argv[2]))
nav = {str(x["name"]).strip().lower() for x in nav_from_requirements(tree)}
lits = {str(x).strip().lower() for x in requirement_literals(tree)}
mine = nav | lits
md_names = {v.strip() for v in _quoted(md) if 2 < len(v.strip()) <= 48}
missing = sorted(v for v in md_names if v.lower() not in mine)
print("markdown 引号名:", len(md_names))
print("我的契约里的名字:", len(mine))
print("markdown 有、我没有:", len(missing))
for m in missing[:25]:
    print("   -", m)