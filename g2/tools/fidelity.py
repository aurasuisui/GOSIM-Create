"""保真度探针：契约来自**需求文本**时能拿到多少？（平台只给需求，不给测试）"""
import sys, re, json
from pathlib import Path
sys.path.insert(0, "g2")
from app.reqcomp.loader import load_pack, extract_targets
from app.gen.seed import nav_targets, load_fixtures, flatten

tree = load_pack("g2/packs/bookstack")
req_text = " ".join(n.text() for n in tree.all_nodes()).lower()
req_targets = []
for n in tree.scorable_leaves():
    for t in extract_targets(n):
        req_targets.append(t.name)
uniq_req = sorted(set(req_targets))

repo_tests = Path("repos/arc-bench/arc-bench/webapp/bookstack/tests")
nav = nav_targets(repo_tests)
nav_names = sorted({n["name"] for n in nav})

print("需求里抽到的靶子（去重）:", len(uniq_req))
print("  样例:", uniq_req[:14])
print()
print("测试导航名（去重）:", len(nav_names))
hit, miss = [], []
for nm in nav_names:
    (hit if nm.lower() in req_text else miss).append(nm)
print("  ✅ 需求文本里能找到的:", hit)
print("  ❌ 需求文本里找不到的:", miss)
print()
fx = load_fixtures(repo_tests)
rows = flatten(fx)
vals = []
for r in rows:
    for v in r["values"].values():
        if isinstance(v, str) and len(v) > 2:
            vals.append(v)
sv, sn = [], []
for v in sorted(set(vals)):
    (sv if v.lower() in req_text else sn).append(v)
print("夹具字面量:", len(set(vals)), " → 需求里能找到:", len(sv), " 找不到:", len(sn))
print("  ❌ 找不到的样例:", sn[:12])