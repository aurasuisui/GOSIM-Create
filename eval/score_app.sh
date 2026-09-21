#!/usr/bin/env bash
# ============================================================
# 本地打分脚本 —— 给一个「生成出来的应用目录」打分
# ============================================================
# 存在理由（PLAN.md §7 M3b-1 的"第一条"）：M3b-1 的目标是 6 个独立的通过率地板，
# 也就是 6 组「生成 → 构建 → 起服务 → 跑测试 → 定向修复」的**多轮**循环。
# 若唯一测试场地是平台，每一轮都要用户上传 + 逐任务点选 + 吃配额 → 用户成为吞吐瓶颈。
# 本地条件已经具备，这个脚本就是那根线。
#
# **冒烟是它的第一步**（PLAN.md §7 那条"闭环必须能发现应用一起来就崩"）：
#   实测教训（2026-09-21）：`glm-5.3-flash` 那组 0/6 里 **5 条是 ERR_CONNECTION_REFUSED**——
#   应用因 `SQLITE_ERROR: no such table` 触发未捕获 rejection 直接退出，
#   而当时的闭环是**静态**的（L1）+ **模型判断**的（自检），**两者都不会把应用真的起起来打一下**。
#   冒烟必须先过，再谈跑测试；否则"0/6"会被误读成"功能没做对"。
#
# 用法：
#   ./eval/score_app.sh <app目录> <应用名> [回合名]
#     应用名 = arc-bench 里的 app（12306 / bookstack / ctrip / keep / prestashop / stackoverflow）
#     回合名 = 给日志与记录用的标签（默认 manual）
#
# 环境变量：PORT（默认 3301）、ARC_TEST_DATE（固定日期，测试是日期相关的）
# 退出码：0 = 冒烟通过且测试跑完；1 = 冒烟失败或测试进程异常
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${1:-}"; APP="${2:-}"; ARM="${3:-manual}"
PORT="${PORT:-3301}"
BENCH_DIR="$ROOT/repos/arc-bench"
export ARC_TEST_DATE="${ARC_TEST_DATE:-2026-09-20}"

log()  { printf '\033[36m[score]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[score]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[score]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

[ -d "$APP_DIR/frontend" ] && [ -d "$APP_DIR/backend" ] || die "不是应用目录（缺 frontend/ 或 backend/）：$APP_DIR"
[ -n "$APP" ] || die "要指定应用名（如 keep / prestashop / stackoverflow）"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/runs/${STAMP}-${APP}-score-${ARM}.log"
JUDGE_LOG="$ROOT/runs/${STAMP}-${APP}-score-${ARM}.judges.log"
exec > >(tee "$OUT") 2>&1

log "打分对象：$APP_DIR"
log "应用：$APP   回合：$ARM   输出：$OUT"
log "ARC_TEST_DATE=$ARC_TEST_DATE（测试是日期相关的，必须固定）"

# ---------- 0. 静置快照（跑前必记）----------
# 为什么：`docs/04` §一要求「只在静置条件下比较」，而本脚本的结果要进 runs/ 记录体系。
# 没有跑前快照就没法按静置条件筛轮次（`load_snapshot` 必须是 free / free-leftover 且 runners=0）。
SNAP_VERDICT="$("$ROOT/eval/bench.sh" status 2>/dev/null | grep -E '空闲|正在跑|过期|疑似' | head -1 | tr -d '\r')"
SNAP_PORT_L="$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -ci listen || true)"
SNAP_PORT_L="${SNAP_PORT_L:-0}"      # grep -c 无匹配时会打印 0 并返回 1，别再 `|| echo 0` 叠一个
LOAD_SNAPSHOT="status='${SNAP_VERDICT:-未知}' port${PORT}_listeners=${SNAP_PORT_L}"
log "0/5 跑前快照：$LOAD_SNAPSHOT"
case "$SNAP_VERDICT" in
  *空闲*) : ;;
  *) warn "    结论行不是「空闲」—— 这一轮**不能**和别的轮次直接比较（见 docs/04 §一）" ;;
esac

# ---------- 0b. 清掉上一轮的数据库 ----------
# 为什么：sqlite 文件在轮次之间**累积**（同一个 `backend/database.db`），
# 与 `docs/04` §一 记的噪声源同源。M3b-1 要跑很多轮，不清就等于每轮读数都被上一轮污染。
DB_FILE="${ARC_DB_FILE:-database.db}"
DB_ABS="$APP_DIR/backend/$(basename "$DB_FILE")"
if [ -e "$DB_ABS" ]; then
  rm -f "$DB_ABS" "$DB_ABS-journal" "$DB_ABS-wal" && log "    已清掉旧库：$DB_ABS"
fi
DELETED="$(find "$APP_DIR/backend" -maxdepth 1 -name '*.db' -print -delete 2>/dev/null | tr '\n' ' ')"
[ -n "$DELETED" ] && log "    另外删掉：$DELETED"

# ---------- 1. 依赖与构建 ----------
log "1/5 装依赖 + 构建前端"
# ⚠️ install/build 失败时必须**看得见原始报错**。原因（2026-09-21 实测）：run e 就死在
# `前端依赖安装失败` 这一行，而当时输出被 `>/dev/null 2>&1` 吞掉了 —— 网络？npm 缓存？
# 文件被占用？完全无从判断，只能重跑一次生成（≈23 万 token）去"碰运气"。
# 现在把捕获的输出 tail -20 打到 stderr（与冒烟失败时打后端日志是同一个写法）。
npm_step() {                       # npm_step <目录> <说明> <命令...>
  local dir="$1" what="$2"; shift 2
  local out
  if ! out="$( cd "$dir" && "$@" 2>&1 )"; then
    err "❌ $what 失败（在 $dir 里跑：$*）"
    err "   —— npm 原始输出（最后 20 行）——"
    printf '%s\n' "$out" | tail -20 >&2
    err "   → 判读：ENOTFOUND/ETIMEDOUT/ECONNRESET = 网络；EACCES/EBUSY/EPERM = 文件被占用"
    err "            ERESOLVE = 依赖冲突；其它 = 看上面原文"
    exit 1
  fi
}
npm_step "$APP_DIR/frontend" "前端依赖安装" npm install --no-audit --no-fund
( cd "$APP_DIR/frontend" && npm run build ) || die "前端构建失败（这一步失败 = 平台上也会失败）"
[ -f "$APP_DIR/frontend/dist/index.html" ] || die "构建产物不在精确路径 frontend/dist/index.html"
npm_step "$APP_DIR/backend" "后端依赖安装" npm install --no-audit --no-fund

# ---------- 2. 数据库准备（尽力而为）----------
log "2/4 准备 E2E 数据库"
if ( cd "$APP_DIR/backend" && npm run db:prepare:e2e >/dev/null 2>&1 ); then
  log "    db:prepare:e2e 成功"
else
  warn "    db:prepare:e2e 失败 —— 不阻断（模板自身的已知 bug，见 docs/11 §五.5.2）；应用启动时会自建 schema"
fi

# ---------- 3. 起服务 + 冒烟 ----------
log "3/4 起服务并冒烟"
PIDS="$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -i listen | awk '{print $NF}' | tr -d '\r' | sort -u)"
for p in $PIDS; do taskkill //F //T //PID "$p" >/dev/null 2>&1 || true; sleep 1; done
( cd "$APP_DIR/backend" && PORT="$PORT" nohup npm run start </dev/null \
    > "$ROOT/runs/.backend-$APP.log" 2>&1 & )
READY=0
for i in $(seq 1 30); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/")" = "200" ]; then
    READY=1; log "    应用就绪（${i}s）HTTP 200"; break
  fi
  sleep 1
done
if [ "$READY" != "1" ]; then
  err "❌ 冒烟失败：30 秒内 GET / 不是 200 —— **不要往下跑判据**（否则 0/N 会被误读成功能问题）"
  err "   后端日志尾部："
  tail -15 "$ROOT/runs/.backend-$APP.log" >&2 || true
  err "   → 这一条属于「应用一起来就崩 / 起不来」，先修它（PLAN.md §7 M3b-1 的闸门之一）"
  # 冒烟失败也**要留记录**：否则"应用起不来"只表现为 runs/ 里少一个文件，
  # 下一个人会以为这一轮没跑过（run e 就是这样：产物在、判据没跑、runs/ 里空手）。
  python - "$ROOT/runs/${STAMP}-${APP}-score-${ARM}.json" "$APP" "$ARM" "$PORT" \
           "${PIPELINE_LLM_MODEL:-${MODEL:-unknown}}" "$LOAD_SNAPSHOT" "$APP_DIR" <<'PY' || true
import json, sys, pathlib
out, app, arm, port, model, snap, app_dir = sys.argv[1:8]
rec = {
    "run_id": pathlib.Path(out).stem, "app": app, "arm_id": arm, "port": int(port),
    "model": model, "load_snapshot": snap, "app_dir": app_dir,
    "tests_total": 0, "tests_passed": 0, "tests_failed": 0, "pass_rate": None,
    "smoke_failed": True,
    "note": "冒烟失败（GET / 不是 200）：后端起不来，按纪律**未跑判据**。"
            "这不是「功能没做对」，是「应用一起来就崩」——先看 runs/.backend-*.log 的报错。",
}
pathlib.Path(out).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  RunRecord（冒烟失败）→ {out}")
PY
  exit 1
fi
log "    附加探活 /api/health → $(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/api/health")"

# ---------- 4. 跑官方测试 ----------
log "4/5 跑官方测试（$APP）"
if [ "$APP" = "quickstart" ]; then
  # quickstart 的判据是 Lab04 的 6 条验收测试（M3a/M3b-0 一直用的那 6 条）。
  # 它需要一个有 @playwright/test 的项目：把 spec + support 复制进 output/（可再生、已忽略），
  # node_modules 直接软链到 arc-bench 的（那里已经装好浏览器）。
  LAB04_SRC="$ROOT/repos/agentic-software-engineering-hackathon/Lab/Lab04/validation"
  RUN_DIR="$ROOT/output/lab04run"
  [ -f "$LAB04_SRC/REQ-1-user-registration.spec.ts" ] || die "找不到 Lab04 判据：$LAB04_SRC"
  mkdir -p "$RUN_DIR"
  cp "$LAB04_SRC/REQ-1-user-registration.spec.ts" "$RUN_DIR/"
  cp -r "$LAB04_SRC/support" "$RUN_DIR/" 2>/dev/null || true
  cat > "$RUN_DIR/playwright.config.js" <<'CFG'
const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: __dirname,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: { headless: true },
});
CFG
  [ -e "$RUN_DIR/node_modules" ] || ln -s "$BENCH_DIR/node_modules" "$RUN_DIR/node_modules"
  ( cd "$RUN_DIR" && E2E_BASE_URL="http://127.0.0.1:$PORT" \
      npx playwright test --reporter=list ) > "$JUDGE_LOG" 2>&1
else
  ( cd "$BENCH_DIR" && npm run test -- --app "$APP" --target-url "http://127.0.0.1:$PORT" ) \
    > "$JUDGE_LOG" 2>&1
fi
grep -E "[0-9]+ (passed|failed|skipped|flaky|did not run)|^Total:|Error:" "$JUDGE_LOG" | tail -20

# ---------- 5. 基础设施异常计数 + RunRecord ----------
# 为什么要单独数一次：`glm-5.3-flash` 那组 0/6 里有 5 条是 `ERR_CONNECTION_REFUSED`
# （后端被 `SQLITE_ERROR` 的未捕获 rejection 打死），而不是"功能没做对"。
# 只在首尾探活是抓不到的——中途崩会伪装成功能失败。
REFUSED="$(grep -c 'ERR_CONNECTION_REFUSED' "$JUDGE_LOG" 2>/dev/null || true)"
REFUSED="${REFUSED:-0}"
NETERR="$(grep -cE 'ECONNREFUSED|socket hang up|net::ERR' "$JUDGE_LOG" 2>/dev/null || true)"
log "5/5 基础设施异常计数：ERR_CONNECTION_REFUSED = $REFUSED"
[ "$REFUSED" != "0" ] && warn "    有连接被拒 —— 这是「应用中途挂了」，**不是**「功能没做对」"
log "    其它网络类错误计行数：${NETERR:-0}"

python "$ROOT/eval/extract_run.py" "$JUDGE_LOG" --app "$APP" --arm "$ARM" --port "$PORT" \
  --model "${PIPELINE_LLM_MODEL:-${MODEL:-unknown}}" \
  --load-snapshot "$LOAD_SNAPSHOT" \
  --json-out "$ROOT/runs/${STAMP}-${APP}-score-${ARM}.json" \
  || warn "RunRecord 抽取失败（判据日志可能没跑完）"

log "跑完。原始日志：$OUT"
log "判据全文：$JUDGE_LOG"
log "后端仍在运行（探活 $(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/")）——要停就 taskkill 占用 $PORT 的进程树"
