#!/usr/bin/env bash
# ============================================================
# 锁的行为测试 —— 「两条入口都验过」的那份证据
# ============================================================
# 存在理由（第二十一轮审核 D）：给 `score_app.sh` 加锁时有一个**已知陷阱**——
# 若调用方已持锁，子进程 PID ≠ 锁持有者 → `acquire_lock` 会把同一轮当成"别人在跑"而拒绝，
# **把判分链弄坏**。所以这条修复必须先有可复现的验证，否则宁可不落地。
#
# 四条判据（每条都必须能**失败**，否则等于没测）：
#   ① 无锁时 `hold` 能拿到，退出后锁被释放
#   ② 有活着的持有者时，第二个 `hold` **被拒**（且不破坏原锁）
#   ③ **继承**：祖先进程持锁时，后代 `hold` 不被拒，且后代退出**不删**祖宗的锁
#   ④ `score_app.sh` 的实际入口（独立跑）走的是"持锁的父进程 + SCORE_LOCK_HELD"这条路，
#      且失败退出后锁被释放
#
# 隔离：全程用临时 `BENCH_LOCK_DIR` + 空闲端口（`ARC_RUNTIME_PORT`/`PORT`），
# 不碰 runs/.bench.lock、不碰 3301 上的真实后端、不花任何 token。
# 用法：bash eval/test_lock.sh
# ============================================================
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TMP="$(mktemp -d 2>/dev/null || echo "$TEMP/locktest.$$")"
mkdir -p "$TMP"
export BENCH_LOCK_DIR="$TMP/lock"
export ARC_RUNTIME_PORT=3399
export PORT=3399
LOCK="$BENCH_LOCK_DIR"

pass=0; fail=0
ok()   { printf '  ✅ %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  ❌ %s\n' "$*"; fail=$((fail+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1（$2）"; else bad "$1：期望 $3，实际 $2"; fi; }
hdr()  { printf '\n=== %s ===\n' "$*"; }

cleanup() { rm -rf "$TMP" 2>/dev/null || true; }
trap cleanup EXIT

# ---------- ① 无锁时能拿到，退出后释放 ----------
hdr "① 无锁 → hold 拿到 → 退出后释放"
rm -rf "$LOCK"
bash "$ROOT/eval/bench.sh" hold "test-①" -- bash -c 'echo "    锁目录存在？$([ -d "'"$LOCK"'" ] && echo yes || echo no)"' >/dev/null 2>&1
check "hold 退出后锁已释放" "$([ -d "$LOCK" ] && echo present || echo absent)" "absent"

# ---------- ② 活着的持有者 → 第二个 hold 被拒 ----------
hdr "② 有活着的持有者 → 第二个 hold 被拒（且不破坏原锁）"
rm -rf "$LOCK"
# 父进程拿锁睡 12 秒（12s > 判定所需，且够短）
bash "$ROOT/eval/bench.sh" hold "test-②持有者" -- sleep 12 >/dev/null 2>&1 &
HOLDER=$!
for _ in $(seq 1 40); do [ -d "$LOCK" ] && break; sleep 0.25; done
OWNER="$(cat "$LOCK/pid" 2>/dev/null || echo '')"
OUT2="$(bash "$ROOT/eval/bench.sh" hold "test-②闯入者" -- true 2>&1)"; RC2=$?
check "闯入者被拒（退出码 ≠ 0）" "$([ "$RC2" -ne 0 ] && echo refused || echo allowed)" "refused"
case "$OUT2" in *"拒绝"*) ok "拒绝理由被打印：$(printf '%s' "$OUT2" | grep -m1 '拒绝' | cut -c1-60)…" ;;
               *) bad "没有打印拒绝理由" ;; esac
check "原锁的持有者没被改" "$(cat "$LOCK/pid" 2>/dev/null || echo '')" "$OWNER"
wait "$HOLDER" 2>/dev/null
check "持有者退出后锁已释放" "$([ -d "$LOCK" ] && echo present || echo absent)" "absent"

# ---------- ③ 继承：祖先进程持锁 → 后代 hold 不被拒，且不删祖宗的锁 ----------
hdr "③ 祖先持锁 → 后代 hold 继承（不拒绝、不释放）"
rm -rf "$LOCK"
cat > "$TMP/child.sh" <<'CHILD'
#!/usr/bin/env bash
set -uo pipefail
echo "    后代看到的锁目录：$([ -d "$BENCH_LOCK_DIR" ] && echo present || echo absent)"
bash "$ROOT/eval/bench.sh" hold "test-③后代" -- true
echo "    后代退出码=$?"
echo "    后代跑完后锁目录：$([ -d "$BENCH_LOCK_DIR" ] && echo present || echo absent)"
CHILD
OUT3="$(ROOT="$ROOT" BENCH_LOCK_DIR="$LOCK" bash "$ROOT/eval/bench.sh" hold "test-③祖先" -- bash "$TMP/child.sh" 2>&1)"
printf '%s\n' "$OUT3" | sed 's/^/    | /'
case "$OUT3" in *"继承"*) ok "后代走的是继承分支" ;; *) bad "后代没有走继承分支（可能被拒或另拿了锁）" ;; esac
case "$OUT3" in *"后代退出码=0"*) ok "后代未被拒（退出码 0）" ;; *) bad "后代退出码非 0 → 判分链会被弄坏" ;; esac
case "$OUT3" in *"后代跑完后锁目录：present"*) ok "后代没有释放祖宗的锁" ;; *) bad "后代的 trap 把祖宗的锁删了" ;; esac
check "祖先退出后锁已释放" "$([ -d "$LOCK" ] && echo present || echo absent)" "absent"

# ---------- ④ score_app.sh 的实际入口 ----------
hdr "④ score_app.sh 独立入口（假应用目录，只为验锁，不跑判分）"
rm -rf "$LOCK"
FAKE="$TMP/fakeapp"; mkdir -p "$FAKE/frontend" "$FAKE/backend"
# 假目录会在 npm 那步失败 → 快速退出；这正好验证"失败退出也会释放锁"
OUT4="$(bash "$ROOT/eval/score_app.sh" "$FAKE" keep locktest 2>&1)"; RC4=$?
case "$OUT4" in *"hold"*|*"已持锁"*) ok "入口走的是 bench.sh hold（持锁父进程）" ;;
                 *) bad "没看到 bench.sh hold 的痕迹"; printf '%s\n' "$OUT4" | head -5 | sed 's/^/    | /' ;; esac
check "score_app 失败退出（预期：假目录装不了依赖）" "$([ "$RC4" -ne 0 ] && echo nonzero || echo zero)" "nonzero"
check "退出后锁已释放" "$([ -d "$LOCK" ] && echo present || echo absent)" "absent"
# 这一步会留下一个 runs/*-keep-score-locktest.log —— 当场删掉，别污染 runs/
rm -f "$ROOT/runs/"*"-keep-score-locktest.log" "$ROOT/runs/"*"-keep-score-locktest.json" 2>/dev/null || true
rm -f "$ROOT/runs/.backend-keep.log" 2>/dev/null || true

hdr "结论"
printf '  通过 %d / 失败 %d\n' "$pass" "$fail"
if [ "$fail" = 0 ]; then
  echo "  ✅ 四条判据全部成立：锁在该拿的时候拿到、该拒的时候拒、继承时不互相破坏、退出时释放"
  exit 0
fi
echo "  ❌ 有判据不成立 —— 不要把 score_app.sh 的加锁当成已验"
exit 1
