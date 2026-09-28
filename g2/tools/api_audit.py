"""静态对比：后端实现了哪些 /api 路由 vs 前端调用了哪些。"""
import re, sys
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "g2/work/bs10")
mounts = {}
app = (out / "backend/src/app.js").read_text(encoding="utf-8", errors="replace")
for m in re.finditer(r"app\.use\(\s*['\"](/api/[A-Za-z0-9_\-]+)['\"]\s*,\s*(\w+)", app):
    mounts.setdefault(m.group(2), []).append(m.group(1))   # 同一 router 可挂多处
impl = set()
for p in (out / "backend/src/routes").glob("*.js"):
    ident = re.sub(r"[^A-Za-z0-9_]", "_", p.stem) + "Router"
    text = p.read_text(encoding="utf-8", errors="replace")
    subs = re.findall(r"router\.(get|post|put|patch|delete)\(\s*['\"`]([^'\"`]*)", text)
    for base in (mounts.get(ident) or ["/api/" + p.stem]):
        for verb, sub in subs:
            path = sub.rstrip("/") or "/"
            impl.add(verb.upper() + " " + (base + path).replace("//", "/").rstrip("/"))
called = {}
for p in (out / "frontend/src").rglob("*.ts*"):
    text = p.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"['\"`](/api/[A-Za-z0-9_\-/]*)", text):
        call = m.group(1).rstrip("/")
        called.setdefault(call, 0)
        called[call] += 1
print("=== 后端实现 ===")
for x in sorted(impl):
    print("  " + x)
print("=== 前端调用（次数）===")
for x, n in sorted(called.items(), key=lambda kv: -kv[1]):
    print(f"  {x}  x{n}")
miss = [c for c in called if not any(i.split(" ", 1)[1].rstrip("/") == c or i.split(" ", 1)[1].rstrip("/").startswith(c + "/") or c.startswith(i.split(" ", 1)[1].rstrip("/")) for i in impl)]
print("=== 前端调用但后端没实现 ===")
for x in sorted(miss):
    print("  " + x)