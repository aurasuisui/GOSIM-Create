#!/usr/bin/env bash
# ============================================================
# 提交包构建 + 合规校验
# ============================================================
# 存在理由：docs/02 §一 的打包规则有 4 条"违反会静默失效"的硬约束
# （zip 第一层必须直接是 main.py；根层不能有 package.json/index.js/index.ts；
#   不塞 .env；不带 __pycache__ 等产物）。这些靠人记一定会错，
# 而且错了不会报错——平台会换一条入口路径，然后 E2E 全挂。
#
# 用法：
#   ./eval/package.sh              构建 output/bundle.zip 并跑合规校验
#   ./eval/package.sh --check-only 只校验已有包
#
# 退出码：全绿 0，任一校验失败 1。
# ============================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/pipeline"
OUT_DIR="$ROOT/output"
BUNDLE="$OUT_DIR/bundle.zip"

log()  { printf '\033[36m[package]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[package]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[package]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

fails=0
check() {   # $1=描述  $2=结果(0/1)
  if [ "$2" = "0" ]; then printf '  ✅ %s\n' "$1"
  else printf '  ❌ %s\n' "$1"; fails=$((fails + 1)); fi
}

build() {
  [ -d "$SRC" ] || die "找不到 $SRC"
  command -v zip >/dev/null 2>&1 || die "没有 zip 命令"
  mkdir -p "$OUT_DIR"
  rm -f "$BUNDLE"

  log "1/2 打包（在 pipeline/ 内部执行，保证第一层就是 main.py）"
  # 只排除产物，不排除任何源码。排除项对齐 docs/02 §一。
  ( cd "$SRC" && zip -r -q "$BUNDLE" . \
      -x '*/__pycache__/*' '__pycache__/*' \
      -x '*.pyc' -x '*.pyo' \
      -x '.env' -x '.env.*' \
      -x '.git/*' '*/node_modules/*' 'node_modules/*' \
      -x '*.egg-info/*' -x '*.db' \
      -x '.arc/*' 'workspace/*' 'output/*' ) || die "zip 失败"
  log "    → $BUNDLE（$(du -h "$BUNDLE" | cut -f1)）"
}

verify() {
  [ -f "$BUNDLE" ] || die "没有包可校验：$BUNDLE"
  log "2/2 合规校验（docs/02 §一）"
  local listing; listing="$(unzip -l "$BUNDLE" | awk '{print $4}' | grep -v '^$')"

  # 1. 第一层必须直接是 main.py（不是 src/main.py、不是套一层目录）
  echo "$listing" | grep -qx 'main.py' && check "第一层有 main.py" 0 || check "第一层有 main.py" 1

  # 2. 根层不能有 package.json / index.js / index.ts —— **只看根层**
  local bad_root
  bad_root="$(echo "$listing" | grep -E '^[^/]+/(package\.json|index\.[jt]s)$' || true)"
  [ -z "$bad_root" ] && check "根层无 package.json / index.js / index.ts" 0 \
                     || { check "根层无 package.json / index.js / index.ts" 1; echo "$bad_root" | sed 's/^/       ↳ /'; }

  # 3. 不塞 .env
  local envf; envf="$(echo "$listing" | grep -E '(^|/)\.env($|\.)' || true)"
  [ -z "$envf" ] && check "包内无 .env" 0 || { check "包内无 .env" 1; echo "$envf" | sed 's/^/       ↳ /'; }

  # 4. 不带产物
  local junk; junk="$(echo "$listing" | grep -E '(__pycache__|\.pyc$|\.git/|node_modules/|\.egg-info/|\.db$|\.arc/)' || true)"
  [ -z "$junk" ] && check "无 __pycache__ / .git / node_modules / *.db / .arc" 0 \
                 || { check "无 __pycache__ / .git / node_modules / *.db / .arc" 1; echo "$junk" | head -5 | sed 's/^/       ↳ /'; }

  # 5. 入口契约要件
  echo "$listing" | grep -qx 'requirements.txt' && check "有 requirements.txt" 0 || check "有 requirements.txt" 1
  echo "$listing" | grep -qx 'arc_runtime/__init__.py' && check "有 arc_runtime/（事件 SDK）" 0 || check "有 arc_runtime/（事件 SDK）" 1

  # 7. 红线 9：**包内不许有题目特定字符串**（AGENTS 硬规则 9）
  #    为什么做成常驻判据：这条是 2026-09-22 第二十七轮审核**人眼 + 一次性脚本**发现的
  #    （19 处 / 4 个文件，其中一处还是功能代码里的按 app 查表）。本项目的结论一贯是
  #    **靠纪律防不住、得靠机器**——所以接进打包闸门，每次构建都判。
  if command -v python >/dev/null 2>&1; then
    if python "$ROOT/eval/check_bundle_strings.py" --zip "$BUNDLE" >/tmp/_redline9.txt 2>&1; then
      check "红线 9：包内无题目特定字符串" 0
    else
      check "红线 9：包内无题目特定字符串" 1
      sed -n '3,12p' /tmp/_redline9.txt | sed 's/^/       ↳ /'
    fi
  else
    warn "没有 python，跳过红线 9 自查"
  fi

  # 6. 源码目录齐（缺了会在平台才炸）
  local d
  for d in reqcompile design generate verify report; do
    echo "$listing" | grep -q "^$d/" && check "有 $d/" 0 || { check "有 $d/" 1; }
  done

  echo
  echo "=== 包体第一层 ==="
  echo "$listing" | awk -F/ 'NF<=2 {print}' | head -20
}

if [ "${1:-}" != "--check-only" ]; then
  build
else
  [ -f "$BUNDLE" ] || die "没有包可校验（先不带 --check-only 跑一次）"
fi
verify

echo
if [ "$fails" != "0" ]; then
  err "❌ 合规校验失败 $fails 项 —— 不要提交这个包"
  exit 1
fi
log "✅ 合规校验全绿：$BUNDLE"
