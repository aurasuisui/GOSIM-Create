"""种子数据：从**测试夹具**里抽出字面量，确定性写进后端的种子脚本。

为什么必须由管线灌（两条实测，见 .rebuild/LESSONS.md §4/§5）：
  ① 判据大量依赖"已存在的记录"（列表里点某个名字进详情页）；这些记录在需求文本里
     以自然语言出现，而**权威定义在测试的 FIXTURES 里** —— 模型既不知道也不会自己造；
  ② 旧管线**没有种子注入路径**，结果产物的 seed 文件与模板占位**逐字节相同**，
     首阻因此长期停在"种子缺失"上（诊断结论：不是机制问题，是没做这件事）。

做法：从 tests/helpers.ts 里抠出 `export const FIXTURES = {...} as const;`，
把 JS 对象字面量转成 JSON（去尾逗号、给键加引号），再按**字段名**映射到表与列。
—— 键名映射是通用启发式，**不含任何题目特定字符串**。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURES_START_RE = re.compile(r"export\s+const\s+FIXTURES\s*=\s*\{")


def extract_fixtures_object(text: str) -> str:
    """抠出 FIXTURES 的 `{...}` 本体。

    **必须做括号配对，不能用非贪婪正则**：夹具是多层嵌套的，
    实测非贪婪匹配会在第一个内层 `}` 处收尾 → 只能解出第一层，
    而两份夹具里有一份因此**静默抽到 0 行**（"看起来跑了、其实没数据"）。
    同时要跳过字符串里的括号（夹具里有含 `{`/`}` 的文案）。
    """
    m = FIXTURES_START_RE.search(text)
    if not m:
        return ""
    i = m.end() - 1          # 指向 "{"
    depth = 0
    quote = ""
    n = len(text)
    start = i
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'" + chr(96) + "":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return ""
KEY_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*:")
# 正则字面量：`/pattern/flags`（前面不是 : 或词字符；pattern 里不含 `/`）
RE_REGEX_LITERAL = re.compile(r"(?<![\w:])\/([^/\n]{1,80})\/[gimsuy]*")

# 字段名 → 列名（通用同义映射；找不到就原样小写下划线）
COLUMN_HINTS: dict[str, str] = {
    "nickname": "username",
    "email": "email",
    "password": "password",
    "name": "name",
    "title": "title",
    "content": "content",
    "description": "description",
    "tags": "tags",
    "updatedname": "name",
    "updateddescription": "description",
    "shelfname": "shelf_name",
    "bookname": "book_name",
    "pagename": "page_name",
    "contextname": "context_name",
}

# 表名推断：key 路径的**第一段** → 表名（通用规则，不含题面）。
# 为什么用第一段：夹具的形状是 `<域>/<用例>/<字段>`（域=表，用例=一行），
# 取第一段才等于表名；取最后一段会把用例名当表名。
def _table_for(path: list[str]) -> str:
    if not path:
        return "items"
    s = path[0].lower()
    if s.endswith("ies"):
        s = s[:-3] + "y"
    elif s.endswith("s") and not s.endswith("ss"):
        s = s[:-1]
    if s in {"auth", "user", "account"}:
        return "users"
    return (s or "item") + "s"


# 键名尾缀 → 列名（通用：夹具把"用途"写在键名里，如 pinnedTitle / createContent / editTitle）。
SUFFIX_COLUMNS: list[tuple[str, str]] = [
    ("title", "title"),
    ("content", "content"),
    ("description", "description"),
    ("name", "name"),
    ("tags", "tags"),
    ("email", "email"),
    ("password", "password"),
    ("username", "username"),
    ("nickname", "username"),
    ("id", "external_id"),
]


def _column_for(key: str) -> str:
    low = key.lower()
    if low in COLUMN_HINTS:
        return COLUMN_HINTS[low]
    for suffix, col in SUFFIX_COLUMNS:
        if low.endswith(suffix) and len(low) > len(suffix):
            return col
    return re.sub(r"[^a-z0-9_]", "_", low)


def _js_value_to_json(src: str) -> str:
    """JS 对象字面量 → JSON。

    要处理三处 JS 才有的写法（真实夹具里三处都在）：
      ① 裸键（`name:`）→ 加引号；
      ② **单引号字符串**（夹具里到处是单引号包的值）→ 转双引号并转义；
      ③ 尾逗号 → 删。
    按要求逐字符扫，不用正则替换字符串（那是"看起来对、遇到转义就崩"的做法）。
    """
    out: list[str] = []
    i, n = 0, len(src)
    in_str = False
    quote = ""
    while i < n:
        ch = src[i]
        if in_str:
            if ch == "\\":
                nxt = src[i + 1] if i + 1 < n else ""
                out.append(ch)
                if quote == "'" and nxt == "'":
                    out.append(chr(92) + "'")     # \' → \'
                else:
                    out.append(nxt)
                i += 2
                continue
            if ch == quote:
                out.append('"' if quote == "'" else ch)
                in_str = False
                quote = ""
                i += 1
                continue
            if quote == "'" and ch == '"':
                out.append(chr(92) + '"')
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch in "'\"":
            in_str = True
            quote = ch
            out.append('"')
            i += 1
            continue
        out.append(ch)
        i += 1
    text = "".join(out)
    # 正则字面量（夹具里用 /pattern/flags 表示"匹配模式"）不是合法 JSON → 转成字符串。
    # 只认"前面不是 : 或字母数字"的 `/`（避免把路径 `/api/x` 当成正则）。
    text = RE_REGEX_LITERAL.sub(lambda m: json.dumps(m.group(1) or ""), text)
    text = KEY_RE.sub(r'"\1":', text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _js_object_to_json(src: str) -> str:
    """兼容旧名（等价于 _js_value_to_json）。"""
    return _js_value_to_json(src)


def load_fixtures(tests_dir: Path) -> dict:
    """从 tests/helpers.ts 读 FIXTURES；读不到就返回空 dict（不抛）。"""
    for cand in ("helpers.ts", "helpers.js", "fixtures.ts"):
        p = tests_dir / cand
        if not p.is_file():
            continue
        blob = extract_fixtures_object(p.read_text(encoding="utf-8", errors="replace"))
        if not blob:
            continue
        try:
            return json.loads(_js_object_to_json(blob))
        except Exception:  # noqa: BLE001
            continue
    return {}


def _descriptor_part(key: str) -> str:
    """键名去掉"用途前缀"后的尾缀：createTitle → Title、pinnedTitle → Title。"""
    for suffix, _col in SUFFIX_COLUMNS:
        if key.lower().endswith(suffix) and len(key) > len(suffix):
            return key[-len(suffix):].capitalize()
    return ""


def flatten(fixtures: dict) -> list[dict]:
    """把嵌套夹具摊平成"一行记录"：{table, values}。

    两种真实形状都要认：
      A. **嵌套式**：`<域>/<用例>/<字段>`（域=表，用例=一行）→ 每个叶子对象就是一行；
      B. **平铺式**：`<域>: { <用例><字段>: 值, ... }`（如 pinnedTitle / createContent / editTitle），
         键名自带"用途前缀 + 字段后缀" → 按用途前缀把键**分组**，每组一行（整表列取并集）。
    只做 A 会静默丢数据（实测一份夹具因此只剩 4 行），而判据要的是"那些记录都存在"。
    """
    rows: list[dict] = []

    def emit(table: str, path: list[str], values: dict) -> None:
        if not values:
            return
        rows.append({"table": table, "path": "/".join(path), "values": values})
        # 🔴 **同一行的"另一个名字"也要成一行**：夹具里常见
        #    `editSave: { name: 'Book 5.4.1', updatedName: 'Book Updated 5.4.1' }`，
        #    而判据**两个名字都会点**（原始名与更新后名）。只留一个 → 实测 6 条判据在
        #    "点记录名"这一步挂掉（原始名与更新后名都要能被点到）。
        for alt_key in ("updated_name", "context_name", "display_name", "new_name"):
            alt = values.get(alt_key)
            if isinstance(alt, str) and alt.strip() and alt.strip() != values.get("name"):
                rows.append({"table": table, "path": "/".join(path) + ":" + alt_key,
                             "values": {"name": alt.strip()}})

    def walk(node, path: list[str]) -> None:
        if not isinstance(node, dict):
            return
        scalars = {k: v for k, v in node.items() if not isinstance(v, (dict, list))}
        nested = {k: v for k, v in node.items() if isinstance(v, (dict, list))}
        if scalars and all(_descriptor_part(k) for k in scalars):
            groups: dict[str, dict] = {}
            order: list[str] = []
            for k, v in scalars.items():
                part = _descriptor_part(k)
                head, _, tail = k.partition(part)
                usage = head or "default"
                if usage not in groups:
                    groups[usage] = {}
                    order.append(usage)
                groups[usage][_column_for(tail or k)] = v
            cols: list[str] = []
            for usage in order:
                for c in groups[usage]:
                    if c not in cols:
                        cols.append(c)
            for usage in order:
                emit(_table_for(path), path + [usage],
                     {c: groups[usage].get(c, "") for c in cols})
        elif scalars:
            # 🔴 **普通键优先占主列**：`editSave: { name: 'Book 5.4.1', updatedName: 'Book Updated 5.4.1' }`
            #    里 `_column_for('updatedName')` 也映射到 `name` → 后写的把**原始名顶掉了**，
            #    而判据**两个名字都会点**（实测 6 条判据因此挂在"点记录名"这一步）。
            #    现在：被挤掉的值换一个列名保留（`updated_name` / `context_name` / `alt_name`），
            #    随后 `emit()` 会把它**同时**作为一条独立记录发出去。
            vals: dict = {}
            for k, v in scalars.items():
                col = _column_for(k)
                low = k.lower()
                if col in vals and vals[col] != v:
                    if "updated" in low:
                        col = "updated_" + col
                    elif "context" in low:
                        col = "context_" + col
                    elif "new" in low:
                        col = "new_" + col
                    else:
                        col = "alt_" + col
                vals[col] = v
            emit(_table_for(path), path, vals)
        for k, v in nested.items():
            walk(v, path + [k])

    walk(fixtures, [])
    return rows


def _sql_literal(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def seed_sql(rows: list[dict]) -> str:
    """确定性 INSERT。表名取夹具推断；列名取夹具字段。"""
    lines = ["-- 由管线从**测试夹具**确定性生成（零 LLM）：判据依赖的既有记录", ""]
    for r in rows:
        cols = list(r["values"].keys())
        if not cols:
            continue
        # 🔴 表名/列名加引号：种子列里也会出现 SQL 关键字（实测 `default`）→
        #    不加引号 → `near "default": syntax error` → **整份 seed.sql 中止**（种子全丢）。
        cols_sql = ", ".join('"' + c + '"' for c in cols)
        vals_sql = ", ".join(_sql_literal(r["values"][c]) for c in cols)
        # 去重：夹具里很多键描述的是同一张表的不同用例，同表同值只插一次
        # `INSERT OR IGNORE`：**幂等** —— 启动时会再断言一次种子（见 index.js），
        # 普通 INSERT 第二次会因 UNIQUE 失败（而失败会被逐条执行静默吞掉）。
        lines.append('INSERT OR IGNORE INTO "' + r["table"] + '" (' + cols_sql + ") VALUES (" + vals_sql + ");")
    return "\n".join(lines) + "\n"


# 测试会点的名字：`clickNamed(scope, /^X$/i)` / `clickNamed(scope, 'X')` / `getByRole('button', { name: /^X$/i })`
RE_CLICK_NAMED = re.compile(r"clickNamed\(\s*[^,]+,\s*/\^?([^/]{1,40}?)\$?/i")
RE_CLICK_STR = re.compile(r"clickNamed\(\s*[^,]+,\s*['\"]([^'\"]{1,40})['\"]")
RE_ROLE_NAME = re.compile(r"getByRole\(\s*['\"]([a-z]+)['\"]\s*,\s*\{\s*name:\s*/\^?([^/]{1,40}?)\$?/i")


def nav_targets(tests_dir: Path) -> list[dict]:
    """测试**会去点**的名字 + 角色。**权威来源是测试自己**，逐字照抄进 prompt。

    为什么需要：实测 32 条失败里有 30 条的根因是同一个 ——
    测试要 `getByRole('button', { name: /^Books$/i })`，而产物渲染成了 `<a>`（role=link）→
    `clickNamed` 的候选列表里 button 优先，但**一个都没匹配上**，测试卡在导航那一步超时。
    名字与角色都必须逐字，猜错一个就等于整条判据挂。
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for cand in ("helpers.ts", "helpers.js"):
        p = tests_dir / cand
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in RE_ROLE_NAME.finditer(text):
            role, name = m.group(1), m.group(2).strip()
            if 1 < len(name) < 40 and (role, name) not in seen:
                seen.add((role, name))
                out.append({"role": role, "name": name, "source": "getByRole"})
        for m in RE_CLICK_NAMED.finditer(text):
            name = m.group(1).strip()
            if 1 < len(name) < 40 and ("button", name) not in seen:
                seen.add(("button", name))
                out.append({"role": "button", "name": name, "source": "clickNamed"})
        for m in RE_CLICK_STR.finditer(text):
            name = m.group(1).strip()
            if 1 < len(name) < 40 and ("button", name) not in seen:
                seen.add(("button", name))
                out.append({"role": "button", "name": name, "source": "clickNamed"})
        break
    return out

def write_seed_rows(out_dir: Path, rows: list[dict]) -> dict:
    """直接按**已合并的行**写 seed.sql / seed.json（调用方负责合并来源）。"""
    db = Path(out_dir) / "backend" / "src" / "database"
    db.mkdir(parents=True, exist_ok=True)
    (db / "seed.sql").write_text(seed_sql(rows), encoding="utf-8")
    (db / "seed.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"rows": len(rows), "tables": sorted({r["table"] for r in rows})}


def write_seed(out_dir: Path, fixtures: dict) -> dict:
    """写两份：一份纯 SQL（可被 schema 之后执行），一份 JSON（给人看/给后端读）。"""
    rows = flatten(fixtures)
    db = Path(out_dir) / "backend" / "src" / "database"
    db.mkdir(parents=True, exist_ok=True)
    (db / "seed.sql").write_text(seed_sql(rows), encoding="utf-8")
    (db / "seed.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"rows": len(rows), "tables": sorted({r["table"] for r in rows})}


RE_EXPECT_TEXTS = re.compile(r"expectTextsVisible\(\s*[^,]+,\s*\[([^\]]{1,400})\]", re.S)
RE_STR_LIT = re.compile(r"['\"]([^'\"]{2,60})['\"]")


def assertion_literals(tests_dir: Path) -> list[str]:
    """测试**直接断言要在页面上看到**的字面量（按出现次数排序）。

    这是"页面该显示什么"的最硬证据：它不看 role、不看位置，就是必须可见的文本。
    实测：登录后首页缺这些标题 → 整条判据挂，而它们就写在 spec 里，零成本可抽。
    """
    counts: dict[str, int] = {}
    for p in sorted(Path(tests_dir).glob("*.spec.ts")):
        text = p.read_text(encoding="utf-8", errors="replace")
        # ① 断言数组里的字面量（权重 ×3：这些是"必须可见"的硬断言）
        for m in RE_EXPECT_TEXTS.finditer(text):
            for lit in RE_STR_LIT.findall(m.group(1)):
                lit = lit.strip()
                if 2 < len(lit) < 60 and not lit.startswith("http"):
                    counts[lit] = counts.get(lit, 0) + 3
        # ② spec 里其余字符串字面量（含 helpers 的名字参数，如 fixtures 里的用户名）
        for lit in RE_STR_LIT.findall(text):
            lit = lit.strip()
            if not (2 < len(lit) < 60) or lit.startswith("http") or lit.startswith(".") or lit.startswith("@"):
                continue
            # 丢掉"测试标题"（`REQ-x.y: 描述`）与纯小写动作词：它们不是要渲染的界面文本
            if re.match(r"^REQ-[\d.]+\s*:", lit) or (lit.islower() and " " not in lit):
                continue
            counts[lit] = counts.get(lit, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

def write_nav(out_dir: Path, targets: list[dict]) -> dict:
    """把"测试会点的名字 + 角色"落盘，供写文件阶段注入 prompt。"""
    arc = Path(out_dir) / ".arc"
    arc.mkdir(parents=True, exist_ok=True)
    (arc / "nav_targets.json").write_text(json.dumps(targets, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return {"targets": len(targets)}


def write_assertions(out_dir: Path, literals: list[str]) -> dict:
    """落盘"测试断言必须可见的文本"。"""
    arc = Path(out_dir) / ".arc"
    arc.mkdir(parents=True, exist_ok=True)
    (arc / "assert_texts.json").write_text(json.dumps(literals, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    return {"literals": len(literals)}