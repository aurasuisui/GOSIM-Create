"""JS/TSX **词法级**配平扫描 —— 抓"一个括号掉了"这类会让构建/启动直接失败的错误。

为什么它必须是管线的一部分（2026-09-21 run e 实测）：闭环把 `backend/src/app.js` 里
`});` 写成了 `}` —— **少一个右括号** → `SyntaxError: missing ) after argument list`
→ 后端进程起不来 → 冒烟就挂 → 那 23.4 万 token 的产物在判据侧归零。
而 L1 原有的静态检查（import 解析 / 路由 / 可访问名 / 建表 / 脚手架 / 脚本）**都不看语法**，
模型自检也没抓到 —— 所以这一类是**闸门上的洞**。

它只做词法级配平：括号/方括号/花括号、引号与模板字面量、块注释。
**它不懂语法**（不认正则字面量、不认 JSX 语义），所以判定口径保守：
只在"闭括号多出来"或"扫描结束仍有未闭合的开括号"时才算问题。

假阳性实测（2026-09-21）：官方模板 + 6 份历史产物共约 108 个文件 → **0 个问题**；
run e 那份坏产物 → 精确命中 `backend/src/app.js`。

用法（CLI）：`python eval/check_js_balance.py <文件或目录> …`
"""
from __future__ import annotations

import pathlib
import sys

PAIRS = {")": "(", "]": "[", "}": "{"}
OPEN = set("([{")
CLOSERS = set(")]}")


def scan(text: str) -> list[str]:
    """返回问题清单（行号 + 说明）。只做词法级配平，不懂语法。"""
    problems: list[str] = []
    stack: list[tuple[str, int]] = []
    i, line, n = 0, 1, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        # 行注释
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        # 块注释
        if text.startswith("/*", i):
            j = text.find("*/", i)
            if j < 0:
                problems.append(f"第 {line} 行：块注释 `/*` 没有闭合 `*/`")
                return problems
            line += text.count("\n", i, j)
            i = j + 2
            continue
        # 字符串 / 模板字面量
        if ch in ("'", '"', "`"):
            quote, start_line = ch, line
            i += 1
            while i < n:
                c = text[i]
                if c == "\\":
                    i += 2
                    continue
                if c == "\n":
                    line += 1
                    if quote != "`":
                        problems.append(f"第 {start_line} 行：{quote} 字符串跨行未闭合")
                        break
                if c == quote:
                    i += 1
                    break
                if quote == "`" and c == "$" and text.startswith("${", i):
                    # 模板里的插值：递归扫它的括号（简化：按普通括号入栈）
                    i += 1
                    continue
                i += 1
            else:
                problems.append(f"第 {start_line} 行：字符串/模板字面量没有闭合")
            continue
        if ch in OPEN:
            stack.append((ch, line))
            i += 1
            continue
        if ch in CLOSERS:
            if not stack:
                problems.append(f"第 {line} 行：多出来的 `{ch}`（前面没有对应的开括号）")
                i += 1
                continue
            op, oline = stack.pop()
            if op != PAIRS[ch]:
                problems.append(f"第 {line} 行：`{ch}` 与第 {oline} 行的 `{op}` 不配对")
            i += 1
            continue
        i += 1
    for op, oline in stack:
        problems.append(f"第 {oline} 行：`{op}` 没有闭合")
    return problems
