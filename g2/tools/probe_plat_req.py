import json, sys
from pathlib import Path
d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig", errors="replace"))
md = d.get("requirements_markdown") or ""
print("markdown len:", len(md))
print("assets_base_url:", d.get("assets_base_url"))
print("references_base_url:", d.get("references_base_url"))
print("=== 前 3000 字 ===")
print(md[:3000])