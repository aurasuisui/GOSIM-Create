#!/usr/bin/env bash
# 「闸门第 0 条」一键跑：同样本 + 同判据 + 同管线，模型 = 平台注入的 deepseek-v4-flash，网关 = 比赛网关。
#
# 用法：
#   bash eval/gate0_run.sh <标签>                # 生成 + 建表检查 + 跑 Lab04 判据
#   bash eval/gate0_run.sh <标签> --reuse-app    # **不生成**：只对 $DIR/app 已有的产物做检查与判分
#
# `--reuse-app` 的存在理由（2026-09-21 实测）：三次尝试 = 207,533 + 307,163 + 233,548 ≈ 74.8 万 token，
# 而**每一次失败都发生在判据侧**（run d 冒烟挂、run e `npm install` 挂）。
# 判据侧一出问题就要重付一次生成 —— 在配额未知的前提下这是最大的出血点。所以把两段解耦。
set -uo pipefail
ROOT="/c/Users/aurasui/Desktop/Anything/GOSIM Create"
TAG="${1:?要一个标签（c / d / e …）}"
shift || true
REUSE=0
for a in "$@"; do
  case "$a" in
    --reuse-app) REUSE=1 ;;
    *) echo "不认识的参数：$a（只支持 --reuse-app）" >&2; exit 2 ;;
  esac
done
TMP="/c/Users/aurasui/AppData/Local/Temp"
DIR="$TMP/m3b1-gate0$TAG"
cd "$ROOT" || exit 1
# ⚠️ Python 是 **Windows 版**，喂给它的路径必须是 `C:\…` 形式。
# 这条踩过（2026-09-21）：内联 Python 里的 `r"$ROOT/pipeline"` 展开成 `/c/Users/…` →
# `sys.path.insert` 指向一个不存在的路径 → `ModuleNotFoundError: No module named 'verify'`
# → **判据 ① 在每一次 gate0_run.sh 调用里都静默失败**（c / d / e 都是），
# 而"建表检查"的数字我一直是手工另跑得到的。现在统一走 cygpath。
ROOT_WIN="$(cygpath -w "$ROOT" 2>/dev/null || echo "$ROOT")"

mkdir -p "$DIR"
# 先腾干净：上一轮遗留的后端会占着 app/backend/database.db，导致 rm 失败、目录不干净
netstat -ano 2>/dev/null | grep ":3301 " | grep -i listen | awk '{print $NF}' | tr -d '\r' | sort -u \
  | while read -r p; do taskkill //F //T //PID "$p" >/dev/null 2>&1 || true; done
sleep 1

if [ "$REUSE" = "1" ]; then
  # ---- 复用路径：不删产物、不调模型 ----
  [ -d "$DIR/app/frontend" ] && [ -d "$DIR/app/backend" ] \
    || { echo "❌ --reuse-app 要求 $DIR/app 已存在（缺 frontend/ 或 backend/）——不生成，直接退出" >&2; exit 1; }
  SRC_METRICS="$(ls -t "$DIR/app/.arc/metrics.jsonl" 2>/dev/null | head -1)"
  echo "=== 复用已有产物：$DIR/app（**本次未发生任何生成调用**）==="
  if [ -n "$SRC_METRICS" ]; then
    echo "    生成成本不记在本轮：它来自 $SRC_METRICS"
    python "$ROOT/eval/aggregate_metrics.py" "$SRC_METRICS" 2>/dev/null | sed -n '2,4p' | sed 's/^/    /'
  else
    echo "    ⚠️ 找不到 $DIR/app/.arc/metrics.jsonl —— 无法引用原始生成成本"
  fi
  {
    echo "复用已有产物：$DIR/app"
    echo "复用时间：$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "本次未发生生成调用（--reuse-app）：不含任何 LLM 调用，token = 0"
    echo "原始生成成本见：${SRC_METRICS:-（缺失）}"
  } > "$DIR/reuse-note.txt"
  echo "    已写 $DIR/reuse-note.txt"
  # 复用轮的 RunRecord 要能看出"这是谁生成的"——把原模型的标识带给 score_app
  if [ -n "$SRC_METRICS" ]; then
    export PIPELINE_LLM_MODEL="$(python - "$SRC_METRICS" <<'PY'
import json, sys
try:
    rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
    print((rows[0].get("model") or "unknown") + "(reused)")
except Exception:
    print("reused")
PY
)"
  fi
else
  # ---- 正常路径：拿网关 key → 重新生成 ----
  GWKEY="$(grep -E '^OPENAI_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'" | tr -d '\r')"
  [ -n "$GWKEY" ] || { echo "拿不到网关 key"; exit 1; }

  rm -rf "$DIR/app" 2>/dev/null || true

  echo "=== 生成（deepseek-v4-flash @ 比赛网关）===" "$DIR"
  PIPELINE_LLM_API_KEY="$GWKEY" \
  PIPELINE_LLM_BASE_URL="https://api.arc-bench.com/v1" \
  PIPELINE_LLM_MODEL="deepseek-v4-flash" \
  PIPELINE_REQ_IDS=REQ-1 \
  PIPELINE_GENERATE=1 \
  python -u pipeline/main.py compile \
    repos/agentic-requirement-compiler/example/ticketbooking-quickstart \
    -o "$DIR/app" > "$DIR/gen.log" 2>&1
  echo "生成 EXIT=$?"

  # 用量记录进 runs/（RunRecord 体系要能引用）
  cp "$DIR/app/.arc/metrics.jsonl" "$ROOT/runs/20260921T-m3b1-gate0$TAG-metrics.jsonl" 2>/dev/null \
    && echo "metrics → runs/20260921T-m3b1-gate0$TAG-metrics.jsonl"
  cp "$DIR/gen.log" "$ROOT/runs/20260921T-m3b1-gate0$TAG-gen.log" 2>/dev/null
fi

echo "=== 判据 ①：崩溃类静态检查（建表 / 注入 / 配平 / 模块系统）==="
APP_WIN="$(cygpath -w "$DIR/app" 2>/dev/null || echo "$DIR/app")"
python - "$APP_WIN" "$ROOT_WIN" <<'PY' || echo "⚠️ 判据 ① 自己出错了（看上面的 traceback），不要当成产物没问题"
import sys, pathlib
app = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(pathlib.Path(sys.argv[2]) / "pipeline"))
from verify.l1 import (check_db_tables, check_schema_injected,
                       check_syntax_balance, check_module_system)
for name, fn in (("db_tables", check_db_tables), ("schema_injected", check_schema_injected),
                 ("syntax_balance", check_syntax_balance), ("module_system", check_module_system)):
    got = fn(app)
    print(f"{'✅' if not got else '❌'} {name:16s} {len(got)} 项",
          [x["detail"][:80] for x in got[:2]])
PY

echo "=== 判据 ②：跑 Lab04 的 6 条（含冒烟）==="
if [ "$REUSE" = "1" ]; then
  ./eval/score_app.sh "$DIR/app" quickstart "gate0$TAG-reuse"
else
  ./eval/score_app.sh "$DIR/app" quickstart "gate0$TAG"
fi
echo "ALL DONE gate0$TAG（reuse=$REUSE）"
