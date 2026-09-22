#!/usr/bin/env bash
# ============================================================
# L1 回归语料脚本 —— 拿**历史产物**当夹具，验证静态检查"该报的报、不该报的不报"
# ============================================================
# 存在理由（`AGENTS.md` 硬规则 16）：**新检查接进闸门之前，先在历史产物语料上跑一遍**，
# 看它是"又宽又松"还是"又紧又假"。踩过两次：
#   · `check_db_tables` 的假阳性让闭环一直在修不存在的问题（占 65% token）
#   · `check_js_balance.py` 指向装过依赖的目录时扫了 7616 个文件、报 322 条误报
#
# 这里的语料是 `%TEMP%` 下的 6+ 份**真实生成产物**（各次 run 留下的），每份都带"已知读数"：
#   m3b0        deepseek-chat，零人工 6/6          → 所有检查都该是 0
#   m3b1-low    flash+low，5/6                     → 已知 meter 缺 aria-label（潜伏，判据没走到那步）
#   m3b1-dryrun deepseek-chat，5/6                 → 同上（已现场验证）
#   m3b1-flashdef / m3b1-gate0b  flash 默认，3/6  → 已知"从没建表"
#   m3b1-glm     glm，0/6（后端起不来）            → 静态度量不出（执行顺序问题）
#   m3b1-gate0d  flash，冒烟挂                     → 已知 ESM/CJS 混用
#   m3b1-gate0e  flash，冒烟挂                     → 已知少一个右括号
#
# **它是"零 token 检查"的回归门**：改完 L1 就跑它，别用生成去验。
#
# 用法：
#   bash eval/regress_l1.sh              # 只用 %TEMP% 下能找到的语料
#   bash eval/regress_l1.sh <目录> …     # 额外/替换的语料（目录里应有 app/ 子目录）
# 退出码：0 = 全部符合预期；1 = 有意外（要么漏报要么误报）
# ============================================================
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

TMP="${TEMP:-/c/Users/aurasui/AppData/Local/Temp}"
# Windows 的 $TEMP 是 `C:\…` 形式，Python 直接用；Git Bash 下转一下更稳
[ -d "$TMP" ] || TMP="/c/Users/aurasui/AppData/Local/Temp"

EXTRA=("$@")
python - "$TMP" "${EXTRA[@]+"${EXTRA[@]}"}" <<'PY'
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd() / "pipeline"))
from verify.l1 import (check_aria_name_sources, check_db_tables, check_module_system,
                       check_schema_injected, check_seed_literals, check_syntax_balance)

tmp = pathlib.Path(sys.argv[1])
extra = [pathlib.Path(a) for a in sys.argv[2:]]
# keep 家族的语料不在 m3b* 前缀里，显式列出（3b 的判别力全靠这几份）
KEEP_FAMILY = ["m2-keep2", "e2-keep2", "e1b-keep4", "r3b-off", "r3b-on", "m2-keep", "e2-keep"]
roots = extra or sorted(
    [p for p in tmp.glob("m3b*/") if (p / "app/frontend").is_dir()] +
    [p for p in (tmp / n for n in KEEP_FAMILY) if (p / "app/frontend").is_dir()],
    key=lambda p: p.name)

# 3b（种子字面量）的输入：**本次子集**里的需求声明的种子。
# 从需求文本现算，不写死字面量——需求一变，回归要跟着变（写死就等于把回归钉在旧需求上）。
KEEP_REQ = pathlib.Path("repos/arc-bench/arc-bench/webapp/keep/requirements/requirements.yaml")
KEEP_SUBSET = {"REQ-2.1", "REQ-2.2"}          # R3b 的冻结配置（PLAN §7）

def keep_subset_seeds() -> list[str]:
    if not KEEP_REQ.is_file():
        return []
    from reqcompile.loader import load_requirement_tree
    from reqcompile.prose import extract_seed_literals
    tree, _report = load_requirement_tree(KEEP_REQ)
    out: list[str] = []
    for n in tree.ordered():
        if not (n.is_leaf and n.scenarios) or n.id not in KEEP_SUBSET:
            continue
        txt = (n.description or "") + " " + " ".join(
            (st.content or "") for sc in n.scenarios for st in sc.steps)
        for s in extract_seed_literals(txt):
            if s not in out:
                out.append(s)
    return out

SEEDS = keep_subset_seeds()

# 期望值：只写"我们确实知道"的那些（证据见各自 docs/runs）。
# None = 没有已知结论 → 只打印，不判对错（避免把"未知"当"错"）。
EXPECT = {
    "m3b0":         {"db_tables": 0, "schema_injected": 0, "module_system": 0, "syntax_balance": 0,
                     "aria_name_sources": 0},
    "m3b1-low":     {"aria_name_sources": 1},          # 已知：meter 缺 aria-label（现场验证过）
    "m3b1-dryrun":  {"aria_name_sources": 1},          # 同一形态，已用探针验证
    "m3b1-flashdef": {"db_tables": 2},                 # 已知：users / sessions 从没建
    "m3b1-gate0b":  {"db_tables": 2},
    "m3b1-gate0d":  {"module_system": 2},              # 已知：两个 auth 文件用了 ESM
    "m3b1-gate0e":  {"syntax_balance": 1},             # 已知：app.js 少一个 `)`
    "m3b1-gate0c":  {"db_tables": 0, "schema_injected": 0, "aria_name_sources": 0},  # 手工补过 aria-label
    "m3b1-glm":     {"db_tables": 0},                  # 它的毛病是**执行顺序**，静态判不出
    # 3b 的已知读数（第二十一轮审核 §2.1 独立复现过；这就是 R3b 那条独立证据的机器版）：
    "m2-keep2":     {"seed_literals": 0},              # REQ-2.1 通过的那两份，种子里字面量都在
    "e2-keep2":     {"seed_literals": 0},
    "e1b-keep4":    {"seed_literals": 2},              # 这两份里一次都没出现（REQ-2.1 挂）
    "r3b-off":      {"seed_literals": 2},
}

checks = [("db_tables", check_db_tables), ("schema_injected", check_schema_injected),
          ("module_system", check_module_system), ("syntax_balance", check_syntax_balance),
          ("aria_name_sources", check_aria_name_sources),
          ("seed_literals", lambda app: check_seed_literals(app, SEEDS))]

bad: list[str] = []
print(f"语料目录：{tmp}")
print(f"3b 的种子输入（keep 子集 {sorted(KEEP_SUBSET)}）：{SEEDS}")
print(f"{'产物':16s} " + "".join(f"{n[:14]:>16s}" for n, _ in checks))
print("-" * 100)
for root in roots:
    app = root / "app"
    if not app.is_dir():
        continue
    cols = []
    for name, fn in checks:
        n = len(fn(app))
        want = EXPECT.get(root.name, {}).get(name)
        mark = " " if want is None else ("✅" if n == want else "❌")
        if want is not None and n != want:
            bad.append(f"{root.name}.{name}: 实际 {n}，期望 {want}")
        cols.append(f"{str(n) + mark:>16s}")
    print(f"{root.name:16s} " + "".join(cols))

print()
if bad:
    print(f"❌ 与预期不符 {len(bad)} 项：")
    for b in bad:
        print("   - " + b)
    sys.exit(1)
print("✅ 全部符合已知读数（该报的报了、不该报的没报）")
PY
