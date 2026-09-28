import sys
sys.path.insert(0, "g2")
from app.gen.defix import convert_templates
BT = chr(96)
multi = "res.send(" + BT + "<!doctype html>\n<html>\n  <body>hi</body>\n</html>" + BT + ");"
out, n = convert_templates(multi)
print("multi:", repr(out[:90]), "|", n)
import subprocess, tempfile, os
p = os.path.join(tempfile.gettempdir(), "t.js")
open(p, "w", encoding="utf-8").write(out)
r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
print("node --check rc:", r.returncode, (r.stderr or "")[:120])