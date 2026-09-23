#!/usr/bin/env bash
# 把一个阶段的成果从工作分支合并进 main —— **一个阶段一个 commit**，而且**不需要 checkout main**。
#
# 为什么不用 `git merge --squash`：那要先 `git switch main`，而这个工作区**被多个会话共用** ——
# 切分支的那段时间里，别的会话的提交会落到另一个分支的父上（本项目已经出过一次
# "两轮互相污染、两份日志都不可信"的事故，见 AGENTS.md 的多会话并发一节）。
# 这里用底层命令直接造 commit 并移动 `refs/heads/main`，**工作区始终停在分支上**。
#
# 用法：  ./eval/stage-merge.sh "阶段名（一句话）"
# 效果：  main 前进一个 commit —— 树 = 当前分支的树；主题 = `阶段: <阶段名>`；
#         正文自动列出分支上那些 commit 的 subject（**子步骤不丢**）。
# 之后：  继续留在工作分支上干活；下一个阶段再合并一次。
#
# ⚠️ 它**不会**跑 git hook（commit-tree 不触发 hook），所以它是"受认可的合并路径"，
#    而不是绕过闸门 —— `阶段: ` 这一行由本脚本自己写上。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NAME="${1:-}"
if [ -z "$NAME" ]; then
  echo "用法: ./eval/stage-merge.sh \"阶段名（一句话）\"" >&2
  exit 2
fi

BRANCH="$(git symbolic-ref --short -q HEAD || true)"
if [ -z "$BRANCH" ]; then
  echo "❌ 处于 detached HEAD —— 先切到工作分支" >&2
  exit 1
fi
if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  echo "❌ 当前就在 $BRANCH 上。阶段合并要在**工作分支**上执行。" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "❌ 工作区不干净 —— 未提交的改动不会被合并进去，先提交或 stash：" >&2
  git status --short | head -10 >&2
  exit 1
fi

MAIN_REF="refs/heads/main"
git rev-parse --verify "$MAIN_REF" >/dev/null 2>&1 || { echo "❌ 没有 main 分支" >&2; exit 1; }
OLD_MAIN="$(git rev-parse "$MAIN_REF")"
TREE="$(git rev-parse 'HEAD^{tree}')"

if git diff --quiet "$OLD_MAIN" HEAD; then
  echo "没有可合并的内容：main 的树与当前分支（$BRANCH）一致。"
  exit 0
fi

BODY="$(git log --format='- %s' --reverse "$OLD_MAIN"..HEAD || true)"
MSG="$(printf '阶段: %s\n\n%s\n' "$NAME" "$BODY")"

NEW="$(printf '%s' "$MSG" | git commit-tree "$TREE" -p "$OLD_MAIN" -F -)"
# 第三个参数 = 期望的旧值：并发下如果有人动了 main，这里会失败而不是覆盖
git update-ref "$MAIN_REF" "$NEW" "$OLD_MAIN"

echo "✅ main 前进一个阶段 commit"
echo "   阶段   : $NAME"
echo "   main   : $OLD_MAIN -> $NEW"
echo "   子步骤 : $(printf '%s' "$BODY" | grep -c '^- ') 条（在正文里）"
echo "   工作区 : 仍在 $BRANCH 上（**没有**切分支）"
echo
echo "提示：main 的树现在 == $BRANCH 的树。继续在 $BRANCH 上干活即可。"
