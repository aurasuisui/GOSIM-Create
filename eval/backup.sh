#!/usr/bin/env bash
# 把整个仓库打成一个 git bundle，写到仓库**外**，当离机/异地备份。
#
# 为什么需要：2026-09-22 用户删掉了 GitHub 仓库（**为了不让它把个人贡献图刷失真**），
# 从此这个工作区**没有远端**——唯一的历史副本就是本地的 `.git/`。
# 而改写历史之后，旧提交只在 reflog 与本地对象里；**reflog 会被 gc 清掉**。
# → 所以"定期打一份 bundle 到仓库外"是现在唯一的历史保全手段。
#
# 用法：./eval/backup.sh [输出目录]        默认写到桌面
#       BUNDLE_DIR=D:/backup ./eval/backup.sh
#
# 恢复：git clone <file>.bundle 新目录    （或 git bundle unbundle 到现有仓库）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-${BUNDLE_DIR:-$HOME/Desktop}}"
mkdir -p "$OUT"

STAMP="$(date +%Y%m%dT%H%M%S)"
FILE="$OUT/GOSIM-Create-$STAMP.bundle"

git -C "$ROOT" bundle create "$FILE" --all >/dev/null
check="$(git -C "$ROOT" bundle verify "$FILE" 2>&1 || true)"
if ! printf '%s' "$check" | grep -qi "is okay"; then
  echo "❌ bundle 校验失败，已删除未验证的文件：" >&2
  printf '%s\n' "$check" >&2
  rm -f "$FILE"
  exit 1
fi

commits="$(git -C "$ROOT" rev-list --count HEAD)"
echo "✅ 备份完成"
echo "   文件: $FILE"
echo "   大小: $(du -h "$FILE" | cut -f1)   提交数: $commits   HEAD: $(git -C "$ROOT" rev-parse --short HEAD)"
echo
echo "提示：把它放到**云同步目录或别的盘**才算真的离机；留在同一块盘上只防误删、不防盘坏。"
