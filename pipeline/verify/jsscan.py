"""JS/TSX **词法级**配平扫描 —— 抓"一个括号掉了"这类会让构建/启动直接失败的错误。

为什么它必须是管线的一部分（2026-09-21 run e 实测）：闭环把 `backend/src/app.js` 里
`});` 写成了 `}` —— **少一个右括号** → `SyntaxError: missing ) after argument list`
→ 后端进程起不来 → 冒烟就挂 → 那 23.4 万 token 的产物在判据侧归零。
而 L1 原有的静态检查（import 解析 / 路由 / 可访问名 / 建表 / 脚手架 / 脚本）**都不看语法**，
模型自检也没抓到 —— 所以这一类是**闸门上的洞**。

它只做词法级配平：括号/方括号/花括号、引号与模板字面量、块注释、**正则字面量**（见下）。
**它不懂语法**（不认 JSX 语义、不认 `const = 1` 这类），所以判定口径保守：
只在"闭括号多出来"或"扫描结束仍有未闭合的开括号"时才算问题。

**准确率实测（拿 `node --check` 当客观裁判，2026-09-21）**：10 份产物的**全部 110 个后端 `.js`**
→ `TP=1 FP=0 TN=109 FN=0`（那 1 个 TP 正是 run e 的 `app.js` 少一个 `)`）。
另：官方模板 + 6 份历史产物 ≈108 个文件 → 0 个问题。

⚠️ **正则字面量是踩出来的**：gate0d 的 `auth_service.js` 里有
`if (!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$/.test(x)) return false;` ——
正则的字符类里同时有 `'` 和反引号。不认正则的扫描器把它读成"单引号字符串跨行未闭合"，
级联报出三条不存在的错误，**而 `node --check` 说那个文件是好的**。
`_regex_at()` 就是为这条加的（`/` 前一个字符是 `( , = : [ ! & | ? { } ; + - * % ~ ^ < >`
或上一个词是 `return`/`typeof`/… 时按正则处理）。**误报比漏报贵**——每条假发现 = 一轮全量修复。

用法（CLI）：`python eval/check_js_balance.py <文件或目录> …`
"""
from __future__ import annotations

import pathlib
import sys

PAIRS = {")": "(", "]": "[", "}": "{"}
OPEN = set("([{")
CLOSERS = set(")]}")


# 正则字面量的判定：`/` 出现在这些**前一个非空白字符**之后时按"正则开始"处理（除号则不然）。
# 为什么必须认正则（实测的假阳性）：gate0d 的 `auth_service.js` 里有一行
#     if (!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$/.test(localPart)) return false;
# 这个正则的字符类里同时有 `'` 和反引号。不认正则的扫描器会把它读成"单引号字符串跨行未闭合"，
# 然后级联报出 `}` 与 `[` 不配对、`{`/`(` 未闭合 —— **而 `node --check` 说这个文件是好的**。
# 这类误报会换来一轮无效修复（"误报比漏报贵"），所以宁可多写这一段。
REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^<>")
REGEX_KEYWORDS = {"return", "typeof", "instanceof", "in", "of", "case", "delete",
                  "void", "new", "do", "else"}


def _regex_at(text: str, i: int, prev_char: str, last_word: str) -> int | None:
    """`text[i] == '/'` 处若是正则字面量，返回结束下标（含 flags）；否则 None。"""
    if prev_char not in REGEX_PRECEDERS and last_word not in REGEX_KEYWORDS:
        return None
    j, n, in_class = i + 1, len(text), False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return None                     # 正则不跨行 → 刚才判断错了，当除号
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "/":
            j += 1
            while j < n and text[j].isalpha():   # flags
                j += 1
            return j
        j += 1
    return None


def scan(text: str) -> list[str]:
    """返回问题清单（行号 + 说明）。只做词法级配平，不懂语法。"""
    problems: list[str] = []
    stack: list[tuple[str, int]] = []
    i, line, n = 0, 1, len(text)
    prev_char = ""            # 上一个有意义的字符（判"正则 vs 除号"）
    last_word = ""            # 上一个标识符（判 `return /re/`）
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch.isspace():
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
        # 正则字面量（必须在"除号"之前判掉）
        if ch == "/":
            end = _regex_at(text, i, prev_char, last_word)
            i = end if end is not None else i + 1
            prev_char, last_word = "/", ""
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
                    i += 1
                    continue
                i += 1
            else:
                problems.append(f"第 {start_line} 行：字符串/模板字面量没有闭合")
            prev_char, last_word = quote, ""
            continue
        if ch in OPEN:
            stack.append((ch, line))
            prev_char, last_word = ch, ""
            i += 1
            continue
        if ch in CLOSERS:
            if not stack:
                problems.append(f"第 {line} 行：多出来的 `{ch}`（前面没有对应的开括号）")
            else:
                op, oline = stack.pop()
                if op != PAIRS[ch]:
                    problems.append(f"第 {line} 行：`{ch}` 与第 {oline} 行的 `{op}` 不配对")
            prev_char, last_word = ch, ""
            i += 1
            continue
        if ch.isalnum() or ch in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            last_word = text[i:j]
            prev_char = text[j - 1]
            i = j
            continue
        prev_char, last_word = ch, ""
        i += 1
    for op, oline in stack:
        problems.append(f"第 {oline} 行：`{op}` 没有闭合")
    return problems
