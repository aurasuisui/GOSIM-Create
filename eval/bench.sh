#!/usr/bin/env bash
# ============================================================
# 评测脚手架 —— 单一入口
# ============================================================
# 存在理由：测量协议要求"每轮测量前必须重置数据库并重启后端"。
# 手工跑测试一定会忘掉这一步，然后所有 A/B 结论都是错的。
# 见 docs/04-测量协议.md
#
# 用法：
#   ./eval/bench.sh setup               首次准备（装依赖、构建前端、装 Chromium）
#   ./eval/bench.sh start   [app]       起参考实现后端（默认 12306）
#   ./eval/bench.sh stop                停后端并确认端口释放
#   ./eval/bench.sh reset   [app]       重置数据库 + 重启后端 ← 每次测量前必须跑
#   ./eval/bench.sh test    [app] [n]   跑 n 轮（默认 1），每轮前自动 reset 并抽 RunRecord
#   ./eval/bench.sh status              当前状态（一条结论行 + 端口、进程、runs/ 最新记录）
#   ./eval/bench.sh verdict             **机器可读**的现场（给脚本/记录用，无 emoji）：
#                                       status=free|free-leftover:<port>|running|stale|ambiguous runners=N port=free|busy listeners=N
#   ./eval/bench.sh hold [描述] -- CMD  替别的脚本拿锁，在里面跑 CMD（判分窗口用）
#   ./eval/bench.sh recover [--force]   崩溃后恢复：停后端 + 清 runner + 删锁
#                                       （先在 status 里确认没人跑；不带 --force 只做预检）
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="$ROOT/repos/arc-bench"
PORT="${ARC_RUNTIME_PORT:-3301}"

# 只有 12306 带参考实现，其余 app 没有 project/ 目录
REF_APP="${ARC_REF_APP:-12306}"
REF_DIR="$BENCH_DIR/arc-bench/webapp/$REF_APP/project"

log()  { printf '\033[36m[bench]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[bench]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[bench]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---------- 进程治理 ----------
# 教训：`npm run start` 会派生出 `node src/index.js` 子进程。
# 只杀 npm 包装进程，真正的服务进程会变成孤儿继续占端口。
# 必须杀进程树，并且杀完要**验证端口真的释放了**。

port_pids() {
  netstat -ano 2>/dev/null \
    | grep ":$PORT " \
    | grep -i listen \
    | awk '{print $NF}' | tr -d '\r' | sort -u
}

kill_tree() {
  local pid="$1"
  if command -v taskkill >/dev/null 2>&1; then
    taskkill //F //T //PID "$pid" >/dev/null 2>&1   # //T = 连同子进程
  else
    kill -TERM "-$pid" >/dev/null 2>&1 || kill -TERM "$pid" >/dev/null 2>&1
  fi
}

stop_backend() {
  local pids round
  for round in 1 2 3; do
    pids="$(port_pids)"
    [ -z "$pids" ] && { log "端口 $PORT 已空闲"; return 0; }
    for p in $pids; do
      log "停掉占用 $PORT 的进程树 PID=$p（第 $round 次尝试）"
      kill_tree "$p"
    done
    sleep 2
  done
  pids="$(port_pids)"
  if [ -n "$pids" ]; then
    err "端口 $PORT 仍被占用：$pids —— 请手工处理"
    return 1
  fi
  log "端口 $PORT 已释放"
}

# ---------- 并发互斥 ----------
# 事故（2026-09-20 实测）：两个 `bench.sh test` 重叠了约 20 秒。
#   10:56:04 runner A 起跑
#   10:56:23 第二个 invocation 的 reset 换了后端（它只杀端口占用者）
#   10:56:24 runner B 起跑
# runner A 没被杀，于是它脚下的库被重置、后端被换掉，却继续跑完。
# 两份日志都长得像正常结果，两份其实都不可信。
#
# 两道闸：① 同一时刻只允许一个 bench 在跑（锁）；
#         ② 每轮开始前扫掉游离 runner（它们不占端口，stop_backend 看不见）。

LOCK_DIR="${BENCH_LOCK_DIR:-$ROOT/runs/.bench.lock}"   # 可用 BENCH_LOCK_DIR 覆盖（测试隔离用）
# 过期/存活判定参数（可调）：
#   ⚠️ 阈值**不能**设成 30 分钟——六 app 全量测试本身就要 15–30 分钟，加生成会远远超过，
#   第一次跑全量就会遇到"锁被自己判过期"。所以默认 2 小时，且判"是否在跑"优先看**心跳**，不看年龄。
LOCK_STALE_SECS="${BENCH_LOCK_STALE_SECS:-7200}"        # 允许清理过期锁的最小锁年龄
HEARTBEAT_FRESH_SECS="${BENCH_HEARTBEAT_FRESH_SECS:-180}"  # 心跳多久算新鲜
HEARTBEAT_PID=""

# ⚠️ 必须用 Windows 看得见的 PID，不能用 bash 的 $$。
#
# 实测（2026-09-20）：Git Bash 里 `$$` 是 MSYS 内部 PID（实测 449、467），
# 而 `tasklist` 只认 Win32 PID（真实 bash.exe 是 20700 这类，两者对不上）。
# 后果：pid_alive 对锁持有者**永远返回 false** → 锁每次都被当"过期"清掉 →
# **这把锁提供的保护实际是零**，而它本该防住那两次 run 作废的事故。
#
# MSYS 用 /proc/<pid>/winpid 做映射。原生 Linux/macOS 没有该文件，
# 那里 $$ 本身有效，回退即可。
self_pid() {
  local w
  w="$(cat "/proc/$$/winpid" 2>/dev/null || true)"
  printf '%s' "${w:-$$}"
}

pid_alive() {
  local pid="$1"
  [ -n "$pid" ] || return 1
  if command -v tasklist >/dev/null 2>&1; then
    tasklist //FI "PID eq $pid" //NH 2>/dev/null | grep -q "$pid"
  else
    kill -0 "$pid" 2>/dev/null
  fi
}

# ---------- 存活证据（判"是不是正在跑"，而不是"锁有多老"）----------
# 事故（2026-09-20 实测）：一轮测量正在跑时，锁里记的 PID 已被复用/消失，
# 于是 status 打印"⚠️ 过期锁，PID=79 已不存在（下次运行会自动清理）"，
# 与此同时端口被占、3 个 runner 活着、日志正在增长 —— 而它的提示语还在建议你跑 `reset`。
# 结论：**不能靠 PID 这条链路单独判死**。现在的判据是四类证据之和。

heartbeat_file() { printf '%s/heartbeat' "$LOCK_DIR"; }

_age_of() {   # $1=文件/目录；不存在 → 999999
  local f="$1" now mt
  [ -e "$f" ] || { printf '999999'; return; }
  now="$(date +%s)"
  mt="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
  printf '%s' "$(( now - mt ))"
}

heartbeat_age() { _age_of "$(heartbeat_file)"; }
heartbeat_fresh() { [ "$(heartbeat_age)" -le "$HEARTBEAT_FRESH_SECS" ]; }
lock_age() { _age_of "$LOCK_DIR"; }
port_busy() { [ -n "$(port_pids)" ]; }

# 心跳：持有者每 20 秒 touch 一次。这样"是否在跑"不再依赖 PID 身份。
# 若持有者进程已经没了，心跳自己退出，避免留下一个"看起来很新鲜"的幽灵锁。
start_heartbeat() {
  stop_heartbeat
  # 同步先写一次：后台子壳要等 pid_alive（tasklist 约 1s）才落笔，
  # 这期间 heartbeat 文件不存在，会让别人看到"锁没有生命迹象"的假象。
  date +%s > "$(heartbeat_file)" 2>/dev/null || true
  ( while :; do
      owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
      if [ -z "$owner" ] || ! pid_alive "$owner"; then exit 0; fi
      date +%s > "$(heartbeat_file)" 2>/dev/null || exit 0
      sleep 20
    done ) >/dev/null 2>&1 </dev/null &
  HEARTBEAT_PID=$!
}

stop_heartbeat() {
  if [ -n "$HEARTBEAT_PID" ]; then
    kill "$HEARTBEAT_PID" 2>/dev/null || true
  fi
  HEARTBEAT_PID=""
}

# 游离 runner 不占端口，只能按命令行特征找。逻辑在 ps1 里（避免嵌套引号地狱）
runner_count() {
  command -v powershell >/dev/null 2>&1 || { printf '0'; return; }
  local n
  n="$(powershell -NoProfile -Command "
    @(Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" -ErrorAction SilentlyContinue |
      Where-Object { \$_.CommandLine -like '*run-playwright*' -or
                     \$_.CommandLine -like '*playwright*cli.js*' -or
                     \$_.CommandLine -like '*workerProcessEntry*' }).Count
  " 2>/dev/null | tr -d '[:space:]')"
  printf '%s' "${n:-0}"
}

# 一条结论：把「锁 / 端口 / runner / 心跳」四类证据合成人话，不要让人自己拼。
#
# 关键：**判"在跑"的锚点是锁**——`test`/`reset`/`start`/`stop` 全部会拿锁，
# 所以"没有锁"就等于"没有 bench 在跑"。端口被占而没锁 = 上一轮留下的后端，
# 那是无害的（下一次 start/reset 会接管），不该报成"需人工确认"。
#
# 返回：free | free-leftover:<端口> | running:<证据> | stale:<锁年龄> | ambiguous:<证据>
lock_verdict() {
  local owner="" why="" n have_lock=0 age
  if [ -d "$LOCK_DIR" ]; then
    have_lock=1
    owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  fi
  n="$(runner_count)"

  if [ "$have_lock" = 0 ]; then
    if [ "$n" != "0" ]; then
      printf 'ambiguous:没有锁，但有 %s 个 playwright runner 活着（上一轮没退干净；下一次 reset 会清）' "$n"
    elif port_busy; then
      printf 'free-leftover:%s' "$PORT"
    else
      printf 'free'
    fi
    return
  fi

  # 有锁 —— 收集"还活着"的证据（任何一条成立都不能清理）
  if [ -n "$owner" ] && pid_alive "$owner"; then
    why="锁持有者 PID=$owner 活着；"
  elif heartbeat_fresh; then
    why="锁心跳仍在更新（$(heartbeat_age)s 前）；"
  fi
  if [ -n "$why" ]; then
    port_busy && why="${why}端口 $PORT 被占；"
    [ "$n" != "0" ] && why="${why}${n} 个 playwright runner 活着；"
    printf 'running:%s' "$why"
    return
  fi

  # 无生命迹象的锁：按 acquire_lock 的同一套四条件判
  age="$(lock_age)"
  why="锁存在（PID=${owner:-未知} 无生命迹象、年龄 ${age}s）"
  port_busy && why="${why}；端口 $PORT 仍被占"
  [ "$n" != "0" ] && why="${why}；${n} 个 runner 活着"
  if port_busy || [ "$n" != "0" ] || [ "$age" -lt "$LOCK_STALE_SECS" ]; then
    printf 'ambiguous:%s → 疑似刚崩或正在跑，需人工确认' "$why"
  else
    printf 'stale:%s → 四条件齐（PID 死、无 runner、端口空闲、超阈值），可清理' "$why"
  fi
}

# 祖先判定：**不能用进程树**——这条是实测出来的（第二十一轮审核 D 的"陷阱"要落地时才发现）。
#
# 第一版实现走 WMI 父链（`Get-CimInstance Win32_Process` 的 ParentProcessId 逐级上溯），
# 三层嵌套（hold → 子脚本 → 孙脚本）下**认不出祖父**：子进程的 parent 指向一个
# **WMI 里查不到名字**的 PID，链在第二跳就断了（实测：`chain: 12052 (bash.exe) -> parent 26304 ()`）。
# Git Bash 会 fork/exec 出 WMI 看不见的中间进程，所以"按祖先链判"在这里不可靠。
# → 改成**环境令牌**：拿锁时写 `$LOCK_DIR/token` 并 `export BENCH_LOCK_TOKEN`，
#   同一轮里的后代天然带着它（环境变量跨进程可靠传播），别人的会话没有。
#   这就是"我是这一轮的一部分"的判据；它只在**继承**这一处生效，其余判据一条没放松。

acquire_lock() {
  local me; me="$(self_pid)"
  if [ -d "$LOCK_DIR" ]; then
    local owner; owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    # 自己已经持有 → 幂等返回（cmd_test 调用 cmd_reset 会走到这里）
    if [ "$owner" = "$me" ]; then
      heartbeat_fresh || start_heartbeat
      return 0
    fi
    # 调用方（本轮的上层脚本）已经持有 → **继承**：不重复拿、也不释放。
    # 只认"令牌与本轮锁一致"这一种情形，不放松其它任何一条判据。
    if [ -n "${BENCH_LOCK_TOKEN:-}" ] && [ -f "$LOCK_DIR/token" ] \
       && [ "$(cat "$LOCK_DIR/token" 2>/dev/null || true)" = "$BENCH_LOCK_TOKEN" ]; then
      INHERITED_LOCK=1
      log "  锁由本轮的调用方持有（令牌匹配）→ 继承：不重复拿、退出时不释放"
      return 0
    fi

    # 任何一类"在跑"的证据成立就拒绝，绝不清理
    local why="" n age
    pid_alive "$owner" && why="锁持有者 PID=$owner 还活着"
    [ -z "$why" ] && heartbeat_fresh && why="锁心跳还在更新（$(heartbeat_age)s 前）"
    [ -z "$why" ] && port_busy && why="端口 $PORT 仍被占用"
    if [ -z "$why" ]; then
      n="$(runner_count)"
      [ "$n" != "0" ] && why="$n 个 playwright runner 活着"
    fi
    if [ -n "$why" ]; then
      err "拒绝：检测到正在运行的测量 —— $why"
      err "并发会让两轮互相重置数据库，产出两份都不可信的结果（已作废过两次 run）。"
      err "先跑 ./eval/bench.sh status 看结论行；确认没人跑之前，不要执行任何 bench 子命令。"
      return 1
    fi

    # 四条件全部成立才允许清理过期锁：PID 已死 且 无 runner 且 端口空闲 且 锁年龄超阈值
    age="$(lock_age)"
    if [ "$age" -lt "$LOCK_STALE_SECS" ]; then
      err "拒绝清理锁：没有活着的迹象，但锁年龄 ${age}s < 阈值 ${LOCK_STALE_SECS}s。"
      err "疑似刚崩或正在跑，需人工确认。"
      err "→ 若确认是**崩溃残留**（会话被强杀），用：./eval/bench.sh recover --force"
      err "  它会把「停后端 + 清 runner + 删锁」一次做完（阈值可用 BENCH_LOCK_STALE_SECS 调整）"
      return 1
    fi
    warn "清理过期锁：PID=${owner:-未知} 已死、无 runner、端口空闲、锁年龄 ${age}s > ${LOCK_STALE_SECS}s"
    rm -rf "$LOCK_DIR"
  fi
  mkdir -p "$LOCK_DIR"
  echo "$me" > "$LOCK_DIR/pid"
  # 本轮令牌：写进锁目录 + `export` 给子进程 —— 后代靠它认出"我在这一轮里"（见上面的说明）
  printf '%s' "$me-$$-$RANDOM" > "$LOCK_DIR/token"
  export BENCH_LOCK_TOKEN="$(cat "$LOCK_DIR/token")"
  start_heartbeat
  return 0
}

release_lock() {
  stop_heartbeat
  # 继承来的锁**不能**由我释放（那会把祖先进程的保护窗口提前关掉）
  if [ "${INHERITED_LOCK:-0}" = "1" ]; then
    log "  锁是继承来的 → 不释放（留给持有者）"
    return 0
  fi
  set_inflight ""; rm -rf "$LOCK_DIR"
}

# ---------- 「认领」自动化（第 5 条）----------
# 靠会话自觉写「在飞工作」栏失败过一次：那一栏写着"无"，而当时有一轮测量正在跑。
# 所以认领改成 bench.sh 的动作：跑 test/reset 时自动写一行，退出时清掉自己那一行。
INFLIGHT_MARK="bench.sh-autoclaim"

set_inflight() {   # $1 = 描述；空串 = 只清除
  local text="${1:-}"
  # 同时追写 runs/.inflight.log：自己的文件、无竞争、留"这轮认领过"的证据。
  # （对 STATUS.md 的认领是读-改-写，有并发编辑者时存在极短窗口的覆盖风险——
  #  第六轮审核指出这一点，所以再加一条只追加、不修改的账。）
  printf '%s | pid=%s | %s\n' "$(date -u +%FT%TZ)" "$(self_pid)" \
    "${text:-RELEASE（清除认领）}" >> "$ROOT/runs/.inflight.log" 2>/dev/null || true
  command -v python >/dev/null 2>&1 || { warn "无 python，跳过「在飞工作」认领"; return 0; }
  python - "$ROOT/STATUS.md" "$INFLIGHT_MARK" "$text" "$(date '+%H:%M')" <<'PY' || warn "「在飞工作」认领失败（不影响测量）"
import pathlib, sys
path, mark, text, hhmm = sys.argv[1:5]
p = pathlib.Path(path)
if not p.exists():
    sys.exit(0)
lines = p.read_text(encoding="utf-8").splitlines()
out, anchor, inserted = [], None, False
for ln in lines:
    if mark in ln:                      # 先丢掉自己上一轮留下的行
        continue
    if anchor is None and "会话/时段" in ln:
        anchor = True
    out.append(ln)
    if anchor and not inserted and ln.strip().startswith("|") and set(ln.strip()) <= set("|-: "):
        if text:                        # 表头分隔行之后插入
            out.append(f"| {text} | **bench.sh 自动认领**（`{mark}`） | {hhmm} | 运行中 |")
        inserted = True
if not inserted:
    sys.stderr.write("no-anchor")
    sys.exit(1)
p.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

# ---------- 崩溃恢复（第六轮审核点名：这是死锁的唯一出口）----------
# 死锁怎么来的：会话被强杀（trap 没跑）→ 锁残留 + 心跳停 + PID 死，
# **但 `nohup` 起的后端仍占着端口** → `acquire_lock` 的 port_busy 条件成立 → 永久拒绝；
# 而 start/stop/reset/test 全都要拿锁 → 常规子命令全部不可用。
#
# 判据（比自动清理宽松，但仍然安全）：**持有者已死 且 心跳已停 且 无 runner**。
# 这三条成立时，"端口被占"只可能是上一轮遗留的后端，不可能是正在跑的测量
# （正在跑的测量一定有：活着的持有者 + 新鲜心跳 + runner）。
# 任一条不成立 → 拒绝。那可能真的在跑，强杀会毁掉一轮测量。
cmd_recover() {
  local force=0 a
  for a in "$@"; do
    case "$a" in
      --force|-f) force=1 ;;
    esac
  done

  local owner="" hb="" n rules_ok=1 why=""
  [ -d "$LOCK_DIR" ] && owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  n="$(runner_count)"
  hb="$(heartbeat_age)"

  if [ -z "$owner" ] || pid_alive "$owner"; then
    rules_ok=0; why="${why}锁持有者${owner:+（PID=$owner）}仍活着或锁无效；"
  fi
  if heartbeat_fresh; then
    rules_ok=0; why="${why}心跳仍在更新（${hb}s 前）；"
  fi
  if [ "$n" != "0" ]; then
    rules_ok=0; why="${why}有 $n 个 playwright runner 活着；"
  fi

  echo "=== bench.sh recover ==="
  echo "锁        : $([ -d "$LOCK_DIR" ] && echo "存在（PID=${owner:-未知}，年龄 $(lock_age)s，心跳 ${hb}s 前）" || echo 无)"
  echo "端口 $PORT : $(port_busy && port_pids | tr '\n' ' ' || echo 空闲)"
  echo "runner    : $n"
  echo

  if [ "$rules_ok" != "1" ]; then
    err "拒绝恢复：$why"
    err "有「活着」的迹象 —— 那可能是一轮**正在跑的测量**，强杀会毁掉它。"
    err "先 ./eval/bench.sh status 看结论行；确认真的没人在跑再回来。"
    return 1
  fi
  if [ "$force" != "1" ]; then
    warn "三项安全检查通过（持有者已死 / 心跳已停 / 无 runner），可以恢复。"
    warn "将执行：停掉占用 $PORT 的进程树 → 清游离 runner → 删锁。"
    warn "**加 --force 才真正执行**（先核对上面现场是否符合预期）："
    warn "    ./eval/bench.sh recover --force"
    return 2
  fi

  log "恢复：停掉端口占用者"
  stop_backend || warn "端口未能释放（可能要手工 taskkill）"
  kill_stale_runners
  rm -rf "$LOCK_DIR"
  set_inflight ""
  log "恢复完成。复核一次：./eval/bench.sh status（应为 🟢 空闲）"
  return 0
}

# 游离 runner 不占端口，只能按命令行特征找。逻辑在 ps1 里（避免嵌套引号地狱）
kill_stale_runners() {
  local ps1="$ROOT/eval/kill-runners.ps1" n
  command -v powershell >/dev/null 2>&1 || return 0
  [ -f "$ps1" ] || return 0
  n="$(powershell -NoProfile -ExecutionPolicy Bypass -File "$ps1" 2>/dev/null | tr -d '[:space:]')"
  n="${n:-0}"
  if [ "$n" != "0" ] && [ -n "$n" ]; then
    warn "清理了 $n 个游离的 playwright runner（上一轮没退干净）"
    sleep 1
  fi
  return 0
}


wait_http() {
  local i
  for i in $(seq 1 20); do
    if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/"; then
      log "后端就绪（HTTP 200，${i}s）"
      return 0
    fi
    sleep 1
  done
  err "后端 20 秒内未就绪，看 runs/.backend-$REF_APP.log"
  return 1
}

ensure_deps() {
  local dir="$1"
  [ -d "$dir/node_modules" ] || { log "$dir 缺依赖，安装中"; (cd "$dir" && npm install) || return 1; }
}

start_backend() {
  local app="${1:-$REF_APP}"
  local dir="$BENCH_DIR/arc-bench/webapp/$app/project/backend"
  [ -d "$dir" ] || die "$app 没有参考实现（无 project/ 目录）"

  ensure_deps "$dir" || return 1
  stop_backend || return 1

  log "启动 $app 后端，PORT=$PORT"
  # 守护化要点：三个 fd 全部脱离调用者的终端/管道，否则
  # `bench.sh start | tail`、`bench.sh start > out.txt` 这类调用会**永远不返回**
  # （tail 等不到 EOF——写端还被这个后台服务持有）。实测踩过一次。
  ( cd "$dir" && PORT="$PORT" nohup npm run start </dev/null \
      > "$ROOT/runs/.backend-$app.log" 2>&1 & )
  wait_http
}

# ---------- 子命令 ----------

cmd_setup() {
  log "1/4 安装基准 runner 依赖"
  (cd "$BENCH_DIR" && npm install) || return 1
  log "2/4 安装 Chromium"
  (cd "$BENCH_DIR" && npx playwright install chromium) || return 1
  log "3/4 审计测试契约"
  (cd "$BENCH_DIR" && npm run test:audit) || return 1
  log "4/4 构建参考实现（$REF_APP）"
  (cd "$REF_DIR/frontend" && npm install && npm run build) || return 1
  (cd "$REF_DIR/backend" && npm install) || return 1
  log "✅ setup 完成"
}

# ⚠️ start / stop 也必须拿锁。
# 理由：`start` 内部会调 stop_backend（按"谁占着端口"杀），
# 于是**另一个会话跑一次 `start` 就能杀掉正在服役的后端**；
# 而 runner 不占端口、不会被杀，它会继续跑完 —— 脚下的库却已经被换掉。
# 这正是那两次 run 作废的机制。stop 同理：它是在杀别人正在用的后端。
cmd_start() {
  shift
  acquire_lock || die "拒绝并发运行"
  trap 'release_lock' EXIT INT TERM
  start_backend "$@"
}

cmd_stop() {
  acquire_lock || die "拒绝并发运行"
  trap 'release_lock' EXIT INT TERM
  stop_backend
}

# `hold`：**替别的脚本拿锁**，在里面跑一条命令。
# 为什么需要它（第二十一轮审核 D）：`score_app.sh` 的判分窗口此前**完全在锁之外**——
# 今天十几轮 keep 判分只靠"端口 + runner"启发式和 `STATUS.md` 在飞栏承担，
# 锁一次都没生效。而 `score_app.sh` 自己拿锁会踩一个坑：
# 若调用方（如 `gate0_run.sh`）已经持锁，**子进程 PID ≠ 锁持有者** → `acquire_lock` 会
# 把同一轮当成"别人在跑"而拒绝，**把整个判分链弄坏**。
# → 所以锁由 `bench.sh`（这个持锁进程）来拿，子脚本用 `SCORE_LOCK_HELD=1` 表示"已有锁"；
#   而"调用方已持锁"那一半由 `acquire_lock` 的**令牌继承**分支兜住（幂等，不再单独拿）。
cmd_hold() {                       # hold [描述] -- <命令...>
  local desc=""
  if [ "${1:-}" = "--" ]; then
    shift
  else
    [ $# -gt 0 ] || die "用法：bench.sh hold [描述] -- <命令...>"
    desc="$1"; shift
    [ "${1:-}" = "--" ] && shift
  fi
  [ $# -gt 0 ] || die "用法：bench.sh hold [描述] -- <命令...>"

  acquire_lock || die "拒绝并发运行"
  trap 'release_lock' EXIT INT TERM
  # 认领只在"这一轮归我"时写：继承来的锁说明祖先已经认领过，
  # 覆盖它会让「在飞工作」栏在子进程先退出时留下一行指向已结束的进程。
  if [ "${BENCH_CLAIMED:-0}" != "1" ] && [ "${INHERITED_LOCK:-0}" != "1" ]; then
    set_inflight "${desc:-hold（PID $(self_pid)）}"
  fi

  log "已持锁（PID $(self_pid)）：${desc:-（无描述）}"
  log "  → 这一段的窗口在锁保护内；子命令退出后自动释放"
  local rc=0
  "$@" || rc=$?
  log "子命令退出码 $rc —— 释放锁"
  exit "$rc"
}

cmd_reset() {
  local app="${1:-$REF_APP}"
  local dir="$BENCH_DIR/arc-bench/webapp/$app/project/backend"
  [ -d "$dir" ] || die "$app 没有参考实现"

  # 单独跑 `reset` 也要挡住并发：reset 会换掉后端和数据库
  acquire_lock || die "拒绝并发运行"
  trap 'release_lock' EXIT INT TERM
  # 被 cmd_test 调用时不认领：它已经写了更具体的行（"test …"），
  # 否则整轮测量期间「在飞工作」栏会错误地显示成一次 reset。
  [ "${BENCH_CLAIMED:-0}" = "1" ] || set_inflight "reset $app（PID $(self_pid)）"

  log "⚠️  重置数据库（测量协议第 1 条）"
  kill_stale_runners
  stop_backend || return 1
  (cd "$dir" && npm run db:prepare:e2e) || return 1
  log "重启后端"
  ( cd "$dir" && PORT="$PORT" nohup npm run start </dev/null \
      > "$ROOT/runs/.backend-$app.log" 2>&1 & )
  wait_http
}

cmd_test() {
  local app="${1:-$REF_APP}"
  local rounds="${2:-1}"
  local i stamp arm outfile snap

  arm="${ARM_ID:-manual}"

  # 环境快照要在**拿锁之前**采：拿锁之后我们自己就成了"在跑"的那一方，
  # 快照就失去意义了。它记录的是**外部世界**当时的状态（第六轮审核要求）。
  #
  # ⚠️ 多轮时从第 2 轮起我们已经持锁，`lock_verdict` 只会报"自己在跑"——
  # 那是**误导**（看起来像外部有测量）。所以从第 2 轮起明确标成 self-held，
  # 只保留仍然有意义的两项：runner 数（会不会有游离的）与端口是否被外人占。
  snap_world() {   # $1=held → 本进程已持锁
    if [ "${1:-no}" = "held" ]; then
      printf 'status=self-held（本序列持锁中，看不到外部）；runners=%s；port=%s；ci=%s；at=%s' \
        "$(runner_count)" \
        "$([ -n "$(port_pids)" ] && echo busy || echo free)" \
        "${CI:-<unset>}" "$(date -u +%FT%TZ)"
    else
      printf 'status=%s；runners=%s；port=%s；ci=%s；at=%s' \
        "$(lock_verdict | cut -d: -f1)" "$(runner_count)" \
        "$([ -n "$(port_pids)" ] && echo busy || echo free)" \
        "${CI:-<unset>}" "$(date -u +%FT%TZ)"
    fi
  }
  snap="$(snap_world)"

  acquire_lock || die "拒绝并发运行"
  trap 'release_lock' EXIT INT TERM
  BENCH_CLAIMED=1   # 声明"整轮归我"：让 cmd_reset 不要用更窄的描述覆盖它
  set_inflight "test $app×$rounds 轮（arm=$arm，PID $(self_pid)）"

  log "开跑前环境快照（外部世界）：$snap"

  for i in $(seq 1 "$rounds"); do
    echo
    log "========== 第 $i/$rounds 轮（app=$app, arm=$arm）=========="
    kill_stale_runners
    cmd_reset "$app" || die "reset 失败，中止"

    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    outfile="$ROOT/runs/${stamp}-${app}-round${i}.log"
    # 第 1 轮用开跑前采的（有外部意义）；从第 2 轮起我们已经持锁，标成 self-held
    [ "$i" -gt 1 ] && snap="$(snap_world held)"

    log "输出 → $outfile"
    log "本轮环境快照：$snap"
    # 注意：过滤必须带 skipped/flaky/did not run，否则"被跳过"会伪装成"不存在"，
    # 分母就对不上了（这是上一版的实际 bug）。
    if ( cd "$BENCH_DIR" && npm run test -- --app "$app" --target-url "http://127.0.0.1:$PORT" ) \
         2>&1 | tee "$outfile" \
              | grep -E "[0-9]+ (passed|failed|skipped|flaky|did not run)|^Total:|Error:" | tail -20; then
      :
    else
      warn "测试进程返回非零（有失败用例是正常的）"
    fi

    # 本轮结束后立刻扫掉 runner，避免它挂到下一轮
    kill_stale_runners

    # 自动抽 RunRecord —— 手写 50 个字段一定退化，所以必须工具化
    if command -v python >/dev/null 2>&1; then
      log "抽取 RunRecord"
      python "$ROOT/eval/extract_run.py" "$outfile" \
        --app "$app" --arm "$arm" --port "$PORT" --round "$i" \
        --load-snapshot "$snap" --ci "${CI:-}" || warn "抽取失败，看日志"
      # 快照也写进日志末尾，让"这一轮的测量条件"和结果同一份文件可查
      printf '\n=== 本轮开始前的环境快照（事后补写）===\n%s\n' "$snap" >> "$outfile"
    fi
  done

  log "完成后清场：./eval/bench.sh stop"
}

# 机器可读的现场（**给脚本用，给记录用**）。
# 为什么需要：`status` 是**给人看的**，而记录里的 `load_snapshot` 要能**机器校验**——
# 实测过两种踩坑：① 按关键词抓结论行会先命中后面的「=== 锁 === 空闲」；
# ② 按 emoji 锚定在脚本里（locale 不同）**匹配不到**。
# → 输出固定三/四个字段，**不翻译、不带 emoji**：
#     status=free|free-leftover:<port>|running|stale|ambiguous  runners=<n>  port=<free|busy>  listeners=<n>
cmd_verdict() {
  local v n pids l
  v="$(lock_verdict)"
  n="$(runner_count)"
  pids="$(port_pids)"
  l="$(printf '%s\n' "$pids" | grep -c . || true)"
  printf 'status=%s runners=%s port=%s listeners=%s\n' \
    "${v%%:*}" "$n" "$([ -n "$pids" ] && echo busy || echo free)" "${l:-0}"
}

cmd_status() {
  # 先给**一条结论行**。原先把三条线索摆出来让人自己拼，还建议"跑 reset 清理"——
  # 而在有测量正在跑时，reset 是唯一不能做的事（实测踩过一次）。
  local verdict owner n pids
  verdict="$(lock_verdict)"
  n="$(runner_count)"
  pids="$(port_pids)"

  echo "=============================================================="
  case "$verdict" in
    free)
      echo "🟢 空闲 —— 可以运行 bench 子命令"
      ;;
    free-leftover:*)
      echo "🟢 可运行 —— 但端口 ${verdict#free-leftover:} 上有一个**上一轮留下的后端**"
      echo "   无害：下一次 start / reset 会接管它。想现在清掉就跑 ./eval/bench.sh stop。"
      ;;
    running:*)
      echo "🟢 正在跑，不要动 —— 禁止一切 bench 子命令（含 reset / stop）"
      echo "   证据：${verdict#running:}"
      echo "   runner 不占端口，所以「端口空闲」不代表没人跑；等它自己结束。"
      ;;
    stale:*)
      echo "🟡 过期锁，可清理 —— 下次运行会自动清掉"
      echo "   证据：${verdict#stale:}"
      echo "   想立刻清：rm -rf \"$LOCK_DIR\"（先确认 status 里没有 runner、端口空闲）"
      ;;
    *)
      echo "🟡 不确定，先人工确认 —— 在确认之前**不要**跑 reset / stop"
      echo "   证据：${verdict#ambiguous:}"
      # 真实条件（2026-09-22 改；旧文写死"都会被拒"，而它只在**有锁**那一支成立）。
      # 判据就在 acquire_lock 里：`if [ -d "$LOCK_DIR" ]` —— 锁为空时它**直接放行**。
      if [ -d "$LOCK_DIR" ]; then
        echo "   ⚠️ 锁存在但无生命迹象：此刻常规子命令会被 acquire_lock **拒绝**（年龄/端口/runner 任一条成立）。"
        echo "      先按证据确认；确属崩溃残留再走 recover。"
      else
        echo "   ⚠️ **锁为空** → acquire_lock 直接放行，reset / stop / test **会真的执行**，"
        echo "      而它们会接管端口、重置数据库（runner 不占端口、不会被杀）——"
        echo "      这正是「别跑」的理由：不是被挡，是**会生效**。等 runner 自己退干净。"
      fi
      echo "   若你确认是崩溃残留（会话被强杀）：先 ./eval/bench.sh recover 预检，通过后加 --force 执行。"
      ;; 
  esac
  echo "=============================================================="

  echo
  echo "=== 端口 $PORT ==="
  [ -n "$pids" ] && echo "占用中：$pids" || echo "空闲"

  echo
  echo "=== 锁 ==="
  if [ -d "$LOCK_DIR" ]; then
    owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    echo "存在：持有者 PID=${owner:-未知}，锁年龄 $(lock_age)s（阈值 ${LOCK_STALE_SECS}s）"
    echo "心跳：$(heartbeat_age)s 前（新鲜阈值 ${HEARTBEAT_FRESH_SECS}s）"
  else
    echo "空闲"
  fi

  echo
  echo "=== 游离 runner（不占端口，但会污染下一轮）==="
  case "$n" in
    0|"") echo "无" ;;
    *)    echo "⚠️  $n 个" ;;
  esac

  echo
  echo "=== runs/ 最近 5 项 ==="
  ls -t "$ROOT/runs" 2>/dev/null | head -5

  echo
  echo "=== 未完成迹象（无 summary 的 log）==="
  local f found=0
  for f in "$ROOT/runs"/*.log; do
    [ -e "$f" ] || continue
    case "$(basename "$f")" in
      .backend-*) continue ;;
      *.INVALID-*) continue ;;   # 已隔离的坏日志，不参与体检
    esac
    if ! grep -qE "[0-9]+ (passed|failed)" "$f" 2>/dev/null; then
      echo "⚠️  $(basename "$f") 没有测试汇总，可能未跑完"
      found=1
    fi
  done
  [ "$found" = 0 ] && echo "无"
}

case "${1:-}" in
  setup)   cmd_setup ;;
  start)   cmd_start "$@" ;;
  stop)    cmd_stop ;;
  reset)   shift; cmd_reset "$@" ;;
  test)    shift; cmd_test "$@" ;;
  hold)    shift; cmd_hold "$@" ;;
  status)  cmd_status ;;
  verdict) cmd_verdict ;;
  recover) shift; cmd_recover "$@" ;;
  *)       sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
