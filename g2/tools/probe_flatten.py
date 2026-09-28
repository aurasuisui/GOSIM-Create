import json, sys
from pathlib import Path
sys.path.insert(0, "g2")
from app.gen import seed as S
fx = S.load_fixtures(Path("repos/arc-bench/arc-bench/webapp/bookstack/tests"))
def find(node, path="") :
    if isinstance(node, dict):
        for k, v in node.items():
            yield from find(v, path + "/" + str(k))
    elif isinstance(node, str) and "5.4.1" in node:
        yield (path, node)
hits = list(find(fx))
print("hits for 5.4.1:", len(hits))
for p, v in hits[:8]:
    print("  ", p, "=", v[:40])
rows = S.flatten(fx)
books = [r for r in rows if r["table"] == "books"]
print("book rows:", len(books))
for r in books[:6]:
    print("   ", r["path"], r["values"])