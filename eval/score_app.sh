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

# ---------- 0. 拿锁（**这一段以前完全在锁之外**）----------
# 第二十一轮审核 D：`bench.sh test` 才是"全局独占资源"，而实际天天在跑的判分路径是本脚本，
# 它全程不拿锁 → 今天十几轮 keep 判分的并发保护**只**由"端口 + runner"启发式和
# `STATUS.md` 在飞栏承担，锁一次都没生效。那次作废两轮的事故正是"两轮判分互相重置数据库"。
#
# ⚠️ 陷阱（必须先说清，否则这个"修复"会把链接弄坏）：
#   若由**调用方**持锁（例如 `gate0_run.sh`），子进程 PID ≠ 锁持有者 →
#   `acquire_lock` 会把同一轮当成"别人在跑"而拒绝。**实测过：`bench.sh test` 不调本脚本**
#   （`grep -n score eval/bench.sh` 无命中），所以那条入口不存在这个问题；
#   而 `gate0_run.sh` 目前也不持锁。
# → 所以做成"**自己起一个持锁的父进程**"：`bench.sh hold` 拿锁 + 心跳 + 退出时释放，
#   再在里面跑本脚本（`SCORE_LOCK_HELD=1` 表示"已经有锁"）。
#   `acquire_lock` 另有"调用方持锁 → 继承"（环境令牌）分支兜住将来有人给调用方加锁的情形。
#
# 🔴 **静置快照必须在拿锁之前采**（2026-09-22 实跑发现）：拿锁之后 `status` 只会报
#    "🟢 正在跑"——那是**我自己**，不是外部世界。第一次带锁的判分（`r4-keep4b`）的记录里
#    `load_snapshot` 就是这个形态（误导），而 `docs/04` §一要求快照能判"静置"。
#    所以：**外层（未持锁）先采快照 → 用环境变量带进去**；内层只用它，并标出"拿锁前采的"。
if [ "${SCORE_LOCK_HELD:-0}" != "1" ]; then
  # 机器可读现场（`bench.sh verdict`，无 emoji 无翻译）——**拿锁之前**采
  _snap_verdict="$("$ROOT/eval/bench.sh" verdict 2>/dev/null | tr -d '\r')"
  exec env SCORE_LOCK_HELD=1 \
    SCORE_LOAD_SNAPSHOT="${_snap_verdict:-status=unknown}" \
    "$ROOT/eval/bench.sh" hold \
    "score $APP arm=$ARM（判分窗口，PID 由 bench.sh 持有）" -- "$ROOT/eval/score_app.sh" "$@"
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/runs/${STAMP}-${APP}-score-${ARM}.log"
JUDGE_LOG="$ROOT/runs/${STAMP}-${APP}-score-${ARM}.judges.log"
exec > >(tee "$OUT") 2>&1

log "打分对象：$APP_DIR"
log "应用：$APP   回合：$ARM   输出：$OUT"
log "ARC_TEST_DATE=$ARC_TEST_DATE（测试是日期相关的，必须固定）"
log "0/5 锁：判分窗口在锁保护内（由 bench.sh hold 持有，PID=$(cat "$ROOT/runs/.bench.lock/pid" 2>/dev/null || echo '?')）"

# ---------- 0. 静置快照（**拿锁之前**采的那个）----------
# 为什么：`docs/04` §一要求「只在静置条件下比较」，而本脚本的结果要进 runs/ 记录体系。
# 没有跑前快照就没法按静置条件筛轮次（`load_snapshot` 必须是 free / free-leftover 且 runners=0）。
# ⚠️ 两层修正（2026-09-22 实跑各踩一次）：
#   ① 快照必须在**拿锁之前**采——拿锁后 `status` 只会报"正在跑"，那是**我自己**不是外部世界；
#   ② 用 `bench.sh verdict` 的**机器可读**形式（`status=free runners=0 port=free listeners=0`）：
#      按关键词抓结论行会先命中后面的「=== 锁 === 空闲」，按 emoji 锚定在脚本里（locale 不同）
#      **匹配不到**。字段口径见审核 §四.F（"别比 status 字面量"）。
SNAP_NOW="$("$ROOT/eval/bench.sh" verdict 2>/dev/null | tr -d '\r')"
LOAD_SNAPSHOT="${SCORE_LOAD_SNAPSHOT:-$SNAP_NOW}"
if [ -n "${SCORE_LOAD_SNAPSHOT:-}" ]; then
  log "0/5 跑前快照（**拿锁之前**采）：$LOAD_SNAPSHOT"
  log "    拿锁后自查（仅供参考，别当成外部世界）：$SNAP_NOW"
else
  log "0/5 跑前快照：$LOAD_SNAPSHOT（⚠️ 没有外层快照 → 这一份是**持锁时**采的，只反映自己）"
fi
case "$LOAD_SNAPSHOT" in
  *status=free*) : ;;
  *) warn "    快照的 status 不是 free（残留后端时会是 free-leftover）—— 这一轮**不能**" \
          "和别的轮次直接比较，除非按 docs/04 §一 的口径标注" ;;
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
