import json, sys
from pathlib import Path
d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig", errors="replace"))
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
(out / "requirements_markdown.md").write_text(d.get("requirements_markdown") or "", encoding="utf-8")
(out / "prerequisites_markdown.md").write_text(d.get("prerequisites_markdown") or "", encoding="utf-8")
meta = {k: v for k, v in d.items() if k not in {"requirements_markdown", "requirements_yaml"}}
(out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
print("saved to", out)
print("markdown bytes:", len(d.get("requirements_markdown") or ""))
print("meta keys:", list(meta.keys()))