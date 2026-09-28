import re
from pathlib import Path
for app in ("keep", "bookstack"):
    d = Path("repos/arc-bench/arc-bench/webapp") / app / "tests"
    tot = 0
    prox = 0
    for p in sorted(d.glob("*.ts")):
        t = p.read_text(encoding="utf-8", errors="replace")
        tot += len(re.findall(r"getByText\(", t))
        prox += len(re.findall(r"getByText\(.{0,200}?getByRole\(", t, re.S))
    print(app, "getByText total:", tot, "| with nearby getByRole:", prox)
    # 看一个真实片段
    for p in sorted(d.glob("*.ts")):
        t = p.read_text(encoding="utf-8", errors="replace")
        i = t.find("getByText(")
        if i >= 0:
            print("   sample:", repr(t[i:i+180]).replace("\\n", " ")[:200])
            break