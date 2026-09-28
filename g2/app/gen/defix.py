"""确定性后处理器：把**模板字符串（反引号）**改写成字符串拼接。

为什么要有它（实测三轮）：
  模型写 `/api/notes/${id}` 这类模板串，而反引号在长文件里**会被截断**（丢掉开引号）→
  esbuild 报 `Expected ";" but found "{"` → **整个前端构建失败** → 那一轮判据全部作废。
  规则（prompt 里禁）+ 判据（静态检查）都上了，但模型仍会写出 7–11 个含反引号的文件，
  定向修复 2 轮收敛不了 → 改成**零 token 的确定性改写**：让它根本活不到构建那一步。

安全性：只处理**成对**的反引号；落单的（真正被截断的那种）原样保留，交给修复轮。
"""
from __future__ import annotations


def _quote(text: str) -> str:
    if "'" not in text:
        return "'" + text + "'"
    if '"' not in text:
        return '"' + text + '"'
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def convert_templates(src: str) -> tuple[str, int]:
    """返回 (改写后的源码, 改写处数)。落单的反引号不动。"""
    out: list[str] = []
    i, n, count = 0, len(src), 0
    BT = chr(96)
    while i < n:
        ch = src[i]
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                out.append(src[i])
                if src[i] == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(src[i:j])
            i = j
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(src[i:j])
            i = j
            continue
        if ch == BT:
            end = _match_backtick(src, i)
            if end < 0:
                out.append(ch)
                i += 1
                continue
            out.append(_rewrite(src[i + 1:end]))
            count += 1
            i = end + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), count


def _match_backtick(src: str, start: int) -> int:
    """从 start（反引号处）找配对的收尾反引号；跳过 ${...} 内的内容。"""
    BT = chr(96)
    i, n, depth = start + 1, len(src), 0
    while i < n:
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if depth == 0 and ch == BT:
            return i
        if ch == "$" and i + 1 < n and src[i + 1] == "{":
            depth += 1
            i += 2
            continue
        if depth > 0 and ch == "}":
            depth -= 1
        i += 1
    return -1


def _rewrite(body: str) -> str:
    """把模板体拆成 字面量 / ${expr} 交替，拼成 + 连接。"""
    parts: list[str] = []
    lit: list[str] = []
    i, n = 0, len(body)

    def flush() -> None:
        if lit:
            parts.append(_quote("".join(lit)))
            lit.clear()

    while i < n:
        ch = body[i]
        if ch == "\\" and i + 1 < n:
            nxt = body[i + 1]
            lit.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        if ch == "$" and i + 1 < n and body[i + 1] == "{":
            flush()
            depth, j = 1, i + 2
            while j < n and depth:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            expr = body[i + 2:j].strip()
            if expr:
                parts.append("(" + expr + ")")
            i = j + 1
            continue
        # ⚠️ 模板串里的**裸换行**在单引号字符串里非法 → 必须转成 `\n` 转义。
        #    实测：模板的 app.js 里有一个多行 HTML 模板串，直接搬裸换行 →
        #    `SyntaxError: Invalid or unexpected token` → 后端起不来（自伤）。
        if ch == "\n":
            lit.append("\\n")
        elif ch == "\r":
            pass
        else:
            lit.append(ch)
        i += 1
    flush()
    return " + ".join(parts) if parts else "''"