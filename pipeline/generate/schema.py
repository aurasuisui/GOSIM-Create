"""建表落位（gate 0 的真实卡点，2026-09-21）。

**为什么这必须是管线的事，而不是"让模型在 `init_db.js` 里改对地方"**：

模板的 `initializeDatabase()` 长这样（`pipeline/templates/web-react-express/backend/src/database/init_db.js`）：

    initPromise = (async () => {
      await runStatement(database, 'PRAGMA foreign_keys = ON;');

      /** Guide model instructions: … */
      return database;          ← 这一行是我们为修上游 bug 加的（scaffold.KNOWN_FIXES）
    })();

`return database;` 紧跟在引导注释**之后**。模型改代码的习惯是**追加到块末尾**，
于是 `CREATE TABLE` 很容易落到 `return database;` **后面** → 成为永不执行的死代码，
表现与"完全没建表"一模一样：注册接口 `SQLITE_ERROR: no such table` →
route 里被吞成 500（用户名永远不出现 → 三条判据挂），
未捕获时直接以未处理 rejection 打死 Node（后面全部 `ERR_CONNECTION_REFUSED`）。

实测（2026-09-21）：`deepseek-v4-flash` 的标定组与闸门 0 组两组产物都是这个形态，
各挂 3/6。**这不是模型的错**——我们要求它"改对地方"，而那个地方是 JS 控制流里的一行。

所以分工改成：

  · **模型只产出纯 SQL 的 `backend/src/database/schema.sql`**（一个独立产物：
    不需要它理解 JS 控制流，也不需要它找插入位置）
  · **管线把它注入**到 `PRAGMA` 之后、`return database;` 之前——**启动时必然执行**

幂等靠 `// ===PIPELINE SCHEMA BEGIN===` / `END` 标记：第二次注入是**替换**标记块，
不是再叠一段（每轮修复后都会重新注入一次）。
"""
from __future__ import annotations

import re
from pathlib import Path

SCHEMA_REL = "backend/src/database/schema.sql"
INIT_DB_REL = "backend/src/database/init_db.js"
BEGIN_MARK = "// ===PIPELINE SCHEMA BEGIN==="
END_MARK = "// ===PIPELINE SCHEMA END==="

# 注入块要用到模板里的这个助手（`init_db.js` 模块级函数）。
# 它不在就说明 init_db.js 已经不是模板形态了——那种情况下注入必然运行不了，
# 直接报出来，不要产出一个"看起来注入了"的产物。
REQUIRED_HELPER = "runStatement"

RE_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)\n```", re.S)


class SchemaInjectionError(RuntimeError):
    """schema.sql 存在但注入不进去——**必须报出来**，不能静默留一个死产物。"""


def strip_fences(text: str) -> str:
    """模型有时会把 SQL 包在 ```sql 里（真进来也不该当语法错误）。"""
    m = RE_FENCE.search(text)
    return m.group(1) if m else text


def split_sql(text: str) -> list[str]:
    """按 `;` 切语句——但要跳过字符串字面量与 SQL 注释里的分号。

    为什么要自己扫而不是 `text.split(';')`：`DEFAULT 'a;b'` 或
    `-- 说明; 备注` 会被切坏，而切坏的后果是**启动时抛 SQL 语法错**，
    又是一次"完全没建表"。
    """
    stmts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                if i + 1 < n and text[i + 1] == quote:   # '' 转义
                    buf.append(text[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i)
            i = n if j < 0 else j + 2
            continue
        if ch == ";":
            stmts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    stmts.append("".join(buf))
    return [s.strip() for s in stmts if s.strip()]


def _js_literal(sql: str) -> str:
    """把一条 SQL 放进 JS 模板字面量里（反引号、`${`、反斜杠都要转义）。"""
    out = sql.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return "`" + out + "`"


def read_schema_statements(output_dir: Path) -> list[str]:
    f = output_dir / SCHEMA_REL
    if not f.is_file():
        return []
    return split_sql(strip_fences(f.read_text(encoding="utf-8", errors="replace")))


def build_block(statements: list[str]) -> str:
    """注入块的正文（不含首尾换行；缩进对齐 IIFE 内部，便于人读）。"""
    rows = "\n".join("      " + _js_literal(s) + "," for s in statements)
    return (
        BEGIN_MARK + "（管线注入；源文件 " + SCHEMA_REL + "，请勿手改这一块）\n"
        "    const PIPELINE_SCHEMA_STATEMENTS = [\n"
        + rows + "\n"
        "    ];\n"
        "    for (const statement of PIPELINE_SCHEMA_STATEMENTS) {\n"
        "      await runStatement(database, statement);\n"
        "    }\n"
        + END_MARK
    )


def _replace_marked_span(text: str, block: str) -> str | None:
    if BEGIN_MARK not in text or END_MARK not in text:
        return None
    start = text.rindex("\n", 0, text.index(BEGIN_MARK)) + 1   # BEGIN 那一行的行首
    end = text.index(END_MARK) + len(END_MARK)
    nl = text.find("\n", end)
    end = len(text) if nl < 0 else nl
    return text[:start] + block + text[end:]


def _insert_before_return(text: str, block: str) -> str | None:
    """插到 **IIFE 内部**那个 `return database;` 之前——IIFE 里唯一的合法插入点。

    ⚠️ 锚点必须带 `(?=[ \\t]*\\}\\)\\(\\);)`：`initializeDatabase()` **函数级**末尾也有一个
    `  return database;`（2 空格缩进）。不带这个约束时会插到那里——那是在
    `await initPromise` **之后**，并发调用者会在建表完成前拿到连接。
    """
    m = re.search(r"^([ \t]*)return database;[ \t]*\n(?=[ \t]*\}\)\(\);)", text, re.M)
    if not m:
        return None
    return text[:m.start()] + block + "\n" + text[m.start():]


def _insert_after_pragma(text: str, block: str) -> str | None:
    """兜底锚点：`PRAGMA` 之后（旧版模板没有 `return database;` 那一行）。"""
    m = re.search(r"^.*PRAGMA foreign_keys.*$", text, re.M)
    if not m:
        return None
    at = m.end()
    return text[:at] + "\n\n" + block + text[at:]


def inject_schema(output_dir: Path, *, log=print) -> dict:
    """把 `schema.sql` 注入 `init_db.js` 的启动流程。幂等，返回过程摘要。

    没有 `schema.sql` 时是**空操作**（老产物/手改产物仍然走模型自己建表那条路）；
    `L1.check_db_tables` 会把"用了却没建"的表报出来，闭环再定向修。
    """
    init_path = output_dir / INIT_DB_REL
    if not init_path.is_file():
        return {"injected": False, "reason": "no-init-db"}

    statements = read_schema_statements(output_dir)
    if not statements:
        exists = (output_dir / SCHEMA_REL).is_file()
        log(f"  ⚠️  {SCHEMA_REL} {'存在但没有 CREATE TABLE 语句' if exists else '不存在'}"
            " —— 建表无处落位（L1 的 db_tables 会报出来，闭环会要求补这个文件）")
        # 文案要准（第二十七轮审核 §二.8）：文件**存在**、只是没有表时，说 "no-schema-file" 会误导
        # （实测踩过：某轮 `schema.sql` 只有一行注释"设计里没有表"，而 reason 写的是文件不存在）
        return {"injected": False,
                "reason": "schema-has-no-statements" if exists else "no-schema-file"}

    text = init_path.read_text(encoding="utf-8")
    if REQUIRED_HELPER not in text:
        raise SchemaInjectionError(
            f"{INIT_DB_REL} 里没有 {REQUIRED_HELPER} 助手——它已经不是模板形态了，"
            "注入的建表语句运行时必然抛 ReferenceError。恢复模板原文再跑。"
        )

    block = build_block(statements)
    for anchor, fn in (("marker-replaced", _replace_marked_span),
                       ("before-return-database", _insert_before_return),
                       ("after-pragma", _insert_after_pragma)):
        got = fn(text, block)
        if got is not None:
            if got != text:
                init_path.write_text(got, encoding="utf-8")
            log(f"  🔧 建表落位：{len(statements)} 条语句 → {INIT_DB_REL}（锚点 {anchor}）")
            for s in statements[:3]:
                log(f"       · {s.splitlines()[0][:88]}")
            if len(statements) > 3:
                log(f"       · … 其余 {len(statements) - 3} 条")
            return {"injected": True, "statements": len(statements), "anchor": anchor,
                    "file": INIT_DB_REL, "source": SCHEMA_REL}

    raise SchemaInjectionError(
        f"{INIT_DB_REL} 里三个锚点（标记块 / `return database;` / PRAGMA）一个都没命中——"
        "文件被整体重写过了，注入无法保证在启动时执行。"
    )
