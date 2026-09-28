import sys, tempfile, os
from pathlib import Path
sys.path.insert(0, r"C:\Users\aurasui\AppData\Local\Temp\g2bundle-chk")
import main
d = Path(tempfile.mkdtemp())
f = d / "x.js"
bt = chr(96)
f.write_text("const u = " + bt + "/api/x/${id}" + bt + ";", encoding="utf-8")
n = main._fix_templates(d)
print("rewritten:", n)
print("content:", f.read_text(encoding="utf-8"))
print("trace ok:", hasattr(main, "cmd_emit"))
