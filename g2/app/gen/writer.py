"""实现生成：**一文件一调用**，每个请求都装得下。

两条实测教训决定了这个形态：
  ① "合并输出越大越容易被网关断连"；
  ② 每个文件只需要它自己那几条需求 → 请求体积天然小，不需要截断。

写文件的规则（平台契约，违反会静默全挂）：
  - backend/src/database/ 下的模板文件由脚手架提供；**建表与种子由管线确定性写 SQL**，
    不让模型写（实测模型既不建表也不写种子，而判据依赖既有记录）；
  - 后端是 CommonJS（require / module.exports），前端 .tsx 才是 ESM；
  - 输出目录里 frontend/ 与 backend/ 必须存在（平台第一道闸）。
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .defix import convert_templates
from .design import Blueprint
from .shell import app_tsx
from .llm import LLMConfig, Ledger, chat, fit, message_chars
from ..reqcomp.planner import Group
from ..reqcomp.spec import ReqSpec, render_block

BACKEND_RULES = """后端规则（Express + CommonJS）：
- 🔴 **创建/更新接口要返回完整对象（含 `id`）**：`res.json({ id: this.lastID, ...body })` 或
  回查一次再返回。前端要靠这个 id 跳详情页（只返回 `{success:true}` 会让新建后那一步卡住）。
- 🔴 **不要用模板字符串（反引号）**：拼 SQL/URL 用 `+` 或参数化；反引号丢失会让**整个后端语法错**（服务起不来）。
- **不要写 import / export**：一律 const x = require(...) 与 module.exports = ...。
- 数据库助手从 "../database" 引：const { all, get, run } = require('../database');
- 🔴 **认证必须用管线给的 `../auth`**（种子与登录校验必须同一套，否则夹具账号永远登不进）：
  `const auth = require('../auth');` → `auth.verifyPassword(user, 明文口令)`、`auth.hashPassword(pw)`、
  `auth.newSessionId()`、`auth.currentUser(req, { get })`、`auth.setSessionCookie(res, sid)`、
- **建表不要在这个文件里做**（schema.sql 已由启动引导器执行）。
- 写操作必须落库，并按会话/权限校验目标对象；错误响应体形状固定。
- 🔴 **只能 require 这些模块**（其余一律不存在，import 了后端就起不来 → 全部判据挂）：
  `express`、`body-parser`、`cors`、`sqlite3`，以及 Node 内置模块（`crypto`/`path`/`fs`/`util` 等）。
  **绝对不要** `bcrypt` / `bcryptjs` / `jsonwebtoken` / `uuid` / `dotenv` / `axios` 这类没装的包；
  需要哈希/校验就用 `crypto` 内置模块（例如 `crypto.createHash`、`crypto.randomUUID`）。
"""

FRONTEND_RULES = """前端规则（React + TypeScript + Vite）：
- 🔴 **表单提交成功后必须让新数据真的出现**：`await fetchList()` 重新拉列表（或把响应里的对象
  push 进本地 state），然后**清空表单/关闭弹窗**。判据是"创建 → 立刻在列表里点它"，
  只 POST 不刷新 = 那条判据必挂（剩下的失败大多是这一类）。
- 🔴 **新建成功后跳到新记录的详情页**：用响应体里的 `id`（`navigate('/books/' + res.data.id)`）。
  只跳列表页会让"下一步点它"多一次找不到。
- 🔴 **提交处理照这个"标准形状"写**（三个动作缺一不可）：
  ```tsx
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const res = await axios.post('/api/shelves', { name, description, tags });
    await fetchShelves();                 // ① 重新拉列表 → 新记录立刻可见、可点
    setMessage('Saved');                  // ② 给个可见反馈
    if (res.data && res.data.id) navigate('/shelves/' + res.data.id);   // ③ 进详情
  };
  ```
  实测：剩下的判据失败几乎全是"创建/编辑流程"，而卡点就在缺①（列表不刷新，新记录点不到）。
- 🔴 **JSX 文本里不要写 `->` 或裸 `>`**：esbuild 会报 `The character ">" ...` → **构建失败**。
  要显示箭头就用 `→`，或者放进花括号里：`{' -> '}`。
- 🔴 **不要用模板字符串（反引号）**：需要拼字符串就用 `+` 或 `String(x)`。
  实测：反引号在生成长代码里**极易被截断/丢失**（丢一个开引号 → `Expected ";" but found "{"` →
  **整个前端构建失败** → 那一轮判据全部作废）。这条规则是"用纪律换稳定性"。

🔴 **认证门（判据的第一步就是登录，做错这一条会连坐几乎所有测试）**：
- 未登录时，首页（以及任何页面）**必须**渲染一个**名为 `Login` 的按钮**，同时再给一个同名 `<a href="/login">`；
  判据第一步是 `clickNamed(page, /^Login$/i)`，找不到它 → 后面每一条都卡在导航超时。
- 未登录时**不要**渲染登录后才该有的入口（列表入口、创建按钮、Dashboard 区块）——
  否则判据会被误导；这些入口**登录后**才出现。
- 后端返回 **401 = "未登录"**，前端要按未登录渲染（给出 Login 入口），**不要**显示"加载失败"这种错误态。
- 登录要有**服务端会话**（cookie 或 token 存库），刷新页面后仍是登录态；
  登录成功后首页要渲染"已登录首页"：把当前用户的**名字**显示出来（用夹具里的用户名逐字），
  并渲染测试断言的那些区块标题（见下面的"必须可见的文本"清单）。

🔴 **列表/记录项要能被点名点到**：
- 每一条记录**同时**渲染一个同名 `<button>` 与一个同名 `<a>`/`<Link>`（判据第一候选是 button）；
  名字必须是**记录自身的字段值**（逐字，例如 `Book 5.1`），**不要**渲染成 `View Book 5.1` 或 `编辑`。
- 名字要渲染在**该元素自己的文本里**，不要拼进句子。

- 用函数组件 + hooks；**只能 import 已装的包**：`react`、`react-dom`、`react-router-dom`、`axios`；
  其余（UI 库、日期库、状态库…）**一律没有装**，import 了前端就构建失败。
- 调后端用相对路径 fetch('/api/...')（同端口托管）。
- 🔴 **页面里绝对不要做"登录门"**：不要写 `if (!user) return <div>请登录</div>` / `if (!isAuthenticated) return …`。
  判据**不登录**就直接点名字（例如打开 Books 列表点 `Book 6.1.1`）；页面一旦早退成登录提示，
  整页空白 → 那一条判据必挂。列表/详情/表单**一律直接渲染**，登录态只影响你额外的提示条。
- **实体值必须渲染在它自己的元素里**（如 <span>{item.name}</span>），不要拼进句子：
  判据用 getByText(值, {exact:true})，拼进句子永远匹配不到。
- 🔴 **导航/操作入口的名字与角色必须逐字对齐**（见下面的"测试会点的名字"清单）。
  判据用 `getByRole(role, {name: /^名字$/i})`：**名字差一个词、或角色不对（把 button 写成 link）就整条挂**。
- 错误消息渲染在一个 role="alert" 的可见元素里。
- 表单控件必须有原生 label 关联：<label htmlFor="x">名</label><input id="x" />。
"""


@dataclass
class FileJob:
    path: str
    kind: str                       # page / api-router / app-shell / schema / plumbing
    purpose: str = ""
    req_ids: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def app_js(router_names: list[str]) -> str:
    """**确定性生成** `backend/src/app.js`：按 router 文件名逐个挂到 `/api/<名字>`。

    为什么不让模型写：实测它把 router 生成成了 `routes/login.js`，
    却把前端页面写成 `fetch('/api/login')`，而 `app.js` 里**没挂**那个 router →
    `POST /api/login` 返回 404 → 登录永远失败（而它看起来"代码都写了"）。
    挂载表是纯粹的结构信息，由计划派生最可靠：文件名就是路径。
    """
    root = Path(__file__).resolve().parents[2]
    tpl = (root / "templates" / "web-react-express" / "backend" / "src" / "app.js").read_text(encoding="utf-8")
    imports = []
    mounts = []
    # 会话路由**先挂**，而且挂两处：`/api` 与 `/api/auth` —— 页面习惯不同命名都能命中；
    # 先挂也保证它自己的 `/login` 覆盖模型写的那一份（管线那套校验才认得种子里的明文口令）。
    if "_session" in router_names:
        imports.append("const _sessionRouter = require('./routes/_session');")
        # 挂四处：模型的页面可能按任何一种习惯拼 URL（实测出现过 `/api/login/me`）
        for prefix in ("/api", "/api/auth", "/api/login", "/api/session"):
            mounts.append(f"app.use('{prefix}', _sessionRouter);")
    for name in sorted(set(router_names)):
        if name == "_session":
            continue
        ident = re.sub(r"[^A-Za-z0-9_]", "_", name) + "Router"
        imports.append(f"const {ident} = require('./routes/{name}');")
        mounts.append(f"app.use('/api/{name}', {ident});")
    tpl = tpl.replace("// route modules imports", "\n".join(imports) or "// (no routers)")
    tpl = tpl.replace("// register routes", "\n".join(mounts) or "// (no routers)")
    return tpl


def _plumbing(name: str) -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "templates" / "plumbing" / name).read_text(encoding="utf-8")


def bootstrap_js() -> str:
    """启动引导器：在 initializeDatabase 之后执行 schema.sql 与 seed.sql。"""
    return _plumbing("bootstrap.js")


def auth_js() -> str:
    """认证唯一入口（种子与登录必须共用同一套校验，否则夹具账号永远登不进）。"""
    return _plumbing("auth.js")


def session_router_js() -> str:
    """会话端点（login/logout/me/check）—— 由管线提供，避免"页面调的端点没实现"。"""
    return _plumbing("session_router.js")


PATH_RE = re.compile(r"/(?:[a-z0-9][a-z0-9\-]*(?:/[a-z0-9][a-z0-9\-]*)*)")
FILELIKE = (".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".html", ".sql", ".png", ".md", ".py")


def paths_in_text(text: str) -> set[str]:
    """需求文本里**逐字出现过**的路由（零 LLM）。

    用"字符串里出现过"，不用分词/关键词——关键词命中会又宽又松。
    """
    out: set[str] = set()
    for m in PATH_RE.finditer(text or ""):
        cand = m.group(0).rstrip(".,;:)'")
        if any(cand.lower().endswith(x) for x in FILELIKE):
            continue
        if cand.count("/") > 4:
            continue
        out.add(cand)
    return out


def _norm(p: str) -> str:
    segs = [s for s in (p or "").split("/") if s and s != "api"]
    return "/" + "/".join(":id" if s.startswith(":") or s.isdigit() or s.startswith("{") else s
                        for s in segs)


def reqs_for_route(groups: list[Group], route_path: str, *, limit: int = 8) -> list[str]:
    """哪些需求**逐字提到过**这条路由。宁少勿多（要的是靶子，不是背景）。"""
    want = _norm(route_path)
    hits: list[str] = []
    fallback: list[str] = []
    tail = want.strip("/").split("/")[-1] if want.strip("/") else ""
    for g in groups:
        for s in g.specs:
            blob = s.render()
            paths = {_norm(p) for p in paths_in_text(blob)}
            if want in paths:
                hits.append(s.req_id)
            elif tail and tail != ":id" and re.search(rf"\b{re.escape(tail)}\b", blob, re.I):
                fallback.append(s.req_id)
    return (hits or fallback)[:limit]


def _root_of(path: str) -> str:
    seg = [p for p in path.split("/") if p and p != "api"]
    root = re.sub(r"[^a-z0-9]+", "-", (seg[0] if seg else "misc").lower()).strip("-")
    return root or "misc"


def _component_name(path: str) -> str:
    seg = [p for p in path.strip("/").split("/") if p and not p.startswith(":")]
    name = "".join(p.capitalize() for p in seg) or "Home"
    return (re.sub(r"[^A-Za-z0-9]", "", name) or "Home") + "Page"


def plan_files(bp: Blueprint, groups: list[Group]) -> list[FileJob]:
    """蓝图 → 要写的文件。**确定性**，零 LLM。"""
    jobs: list[FileJob] = []
    by_root: dict[str, list[dict]] = {}
    for e in bp.api_endpoints:
        by_root.setdefault(_root_of(e["path"]), []).append(e)
    for root, eps in by_root.items():
        jobs.append(FileJob(path=f"backend/src/routes/{root}.js", kind="api-router",
                            purpose="/api/" + root + " 下的端点",
                            req_ids=reqs_for_route(groups, "/" + root, limit=10),
                            extra={"endpoints": eps}))
    by_comp: dict[str, dict] = {}
    for r in bp.routes:
        comp = re.sub(r"[^A-Za-z0-9_]", "", str(r.get("component") or "")) or _component_name(r["path"])
        if not re.search(r"(Page|Form|Editor|List|Detail)$", comp):
            comp += "Page"
        slot = by_comp.setdefault(comp, {"routes": [], "purpose": ""})
        slot["routes"].append(r["path"])
        slot["purpose"] = slot["purpose"] or str(r.get("purpose") or "")
    for comp, slot in by_comp.items():
        reqs: list[str] = []
        for p in slot["routes"]:
            for rid_ in reqs_for_route(groups, p, limit=6):
                if rid_ not in reqs:
                    reqs.append(rid_)
        field_targets: list[str] = []
        button_targets: list[str] = []
        for g in groups:
            for sp in g.specs:
                if sp.req_id not in reqs:
                    continue
                for t in sp.targets:
                    if not t.name:
                        continue
                    if (t.role or "") in {"textbox", "searchbox", "checkbox", "combobox", "radio", "placeholder"}:
                        if t.name not in field_targets:
                            field_targets.append(t.name)
                    elif (t.role or "") == "button":
                        if t.name not in button_targets:
                            button_targets.append(t.name)
        jobs.append(FileJob(path=f"frontend/src/pages/{comp}.tsx", kind="page",
                            purpose="路由 " + ", ".join(slot["routes"]) + "：" + slot["purpose"],
                            req_ids=reqs[:10],
                            extra={"routes": slot["routes"], "field_targets": field_targets[:12],
                                   "button_targets": button_targets[:16]}))
    jobs.append(FileJob(path="frontend/src/App.tsx", kind="shell-tsx",
                        purpose="壳层 + 导航 + 登录态门（**由管线确定性生成**）",
                        extra={"routes": bp.routes}))
    if bp.db_tables:
        jobs.append(FileJob(path="backend/src/database/schema.sql", kind="schema",
                            purpose="按结构设计建表", extra={"tables": bp.db_tables}))
    jobs.append(FileJob(path="backend/src/database/bootstrap.js", kind="plumbing",
                        purpose="在 initializeDatabase 之后执行 schema.sql 与 seed.sql"))
    jobs.append(FileJob(path="backend/src/database/seed_db.js", kind="plumbing",
                        purpose="种子入口：**只执行 seed.sql**（防止模型自带的默认数据覆盖预置数据）"))
    jobs.append(FileJob(path="backend/src/index.js", kind="plumbing",
                        purpose="后端入口：**带防崩护栏**（未处理 rejection 不再杀进程）"))
    jobs.append(FileJob(path="backend/src/auth.js", kind="plumbing",
                        purpose="认证唯一入口：hashPassword / verifyPassword / 会话工具"))
    jobs.append(FileJob(path="backend/src/routes/_session.js", kind="plumbing",
                        purpose="会话端点（login/logout/me/check），挂在 /api 与 /api/auth 两处"))
    # ⚠️ app.js 必须**最后**建：它的挂载表要看到**全部** routes/ 下的文件，
    # 包括上面刚追加的 `_session.js`。
    # 实测踩过两次：① 漏掉 `_session` → `/api/login` 404；② 顺序放前面 → 一样漏。
    routers = [j.path.rsplit("/", 1)[-1][:-3] for j in jobs
               if j.path.startswith("backend/src/routes/")]
    jobs.append(FileJob(path="backend/src/app.js", kind="app-shell-deterministic",
                        purpose="挂载所有 router（**由管线按文件名生成**，不让模型写挂载表）",
                        extra={"routers": routers}))
    return jobs


RE_SQL_FROM = re.compile(r"FROM\s+([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)?", re.I)
RE_SQL_QUALIFIED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
RE_SQL_INSERT = re.compile(r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.I)
SQL_KEYWORDS = {"select", "from", "where", "join", "order", "group", "limit", "insert", "into",
                "values", "update", "set", "delete", "on", "and", "or", "as", "count", "sum",
                "coalesce", "datetime", "strftime", "lower", "upper", "distinct", "case", "when",
                "then", "else", "end", "left", "inner", "outer", "pragma", "table", "create", "if",
                "not", "exists", "text", "integer", "primary", "key", "unique", "default", "null"}


def sql_columns(out_dir: Path) -> dict[str, list[str]]:
    """从**产物自己的 SQL** 里反推"它以为表里有哪些列"。

    为什么必须做：实测模型写 `SELECT p.updated_at FROM pages p`，而按蓝图生成的表里没有这一列 →
    `SQLITE_ERROR: no such column` → 整个 `/api/dashboard` **500** → 首页那一块永远渲染不出来
    （而静态检查、构建、起服务全绿）。**SQL 才是它要的 schema**，所以让建表语句去适配它。
    """
    cols: dict[str, list[str]] = {}
    for p in (Path(out_dir) / "backend" / "src").rglob("*.js"):
        text = p.read_text(encoding="utf-8", errors="replace")
        alias2table: dict[str, str] = {}
        for m in RE_SQL_FROM.finditer(text):
            table, alias = m.group(1).lower(), (m.group(2) or "").lower()
            if table in SQL_KEYWORDS:
                continue
            alias2table[alias or table] = table
            cols.setdefault(table, [])
        for m in RE_SQL_INSERT.finditer(text):
            table = m.group(1).lower()
            if table in SQL_KEYWORDS:
                continue
            slot = cols.setdefault(table, [])
            for c in m.group(2).split(","):
                name = c.strip().strip("`\"'")
                if name and name not in slot:
                    slot.append(name)
        for m in RE_SQL_QUALIFIED.finditer(text):
            alias, col = m.group(1).lower(), m.group(2).lower()
            table = alias2table.get(alias)
            if not table or col in SQL_KEYWORDS:
                continue
            slot = cols.setdefault(table, [])
            if col not in slot:
                slot.append(col)
    # 所有表都补上常见的审计列（模型默认它们存在，代价只是一个 TEXT 列）
    for table in list(cols):
        for c in ("created_at", "updated_at", "status"):
            if c not in cols[table]:
                cols[table].append(c)
    return cols

def _norm_col(name: str) -> str:
    """列名归一：`createdAt` → `created_at`、非法字符 → `_`、**小写**。

    为什么必须做（实测，代价极大）：SQLite 的列名**大小写不敏感**，
    而模型写 `createdAt`、管线又补了审计列 `created_at` →
    `duplicate column name: createdat` → **整份 schema 执行中止** →
    之后所有表都没建成 → `/api/notes` 直接 `no such table`（整个 app 废掉）。
    """
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name or "").strip())
    s = re.sub(r"[^A-Za-z0-9_]", "_", s).strip("_").lower()
    return s or "col"


def _dedupe_cols(cols: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in cols:
        key = _norm_col(c)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _table_columns(bp: Blueprint) -> dict[str, list[str]]:
    """每张表的列 = 蓝图列的并集。"""
    out: dict[str, list[str]] = {}
    for t in bp.db_tables:
        name = re.sub(r"[^A-Za-z0-9_]", "_", str(t.get("name") or "table"))
        cols = [re.sub(r"[^A-Za-z0-9_]", "_", str(c).strip()) for c in (t.get("columns") or [])]
        cols = [c for c in cols if c]
        slot = out.setdefault(name, [])
        for c in cols:
            if c not in slot:
                slot.append(c)
    return out


def _merge_columns(*maps: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in maps:
        for table, cols in (m or {}).items():
            slot = out.setdefault(table, [])
            for c in cols:
                if c not in slot:
                    slot.append(c)
    return out


def seed_columns(out_dir: Path) -> dict[str, list[str]]:
    """种子里用到的 (表 → 列)。建表时必须并进来，否则 INSERT 整体失败。"""
    p = Path(out_dir) / "backend" / "src" / "database" / "seed.json"
    if not p.is_file():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, list[str]] = {}
    for r in rows if isinstance(rows, list) else []:
        table = str(r.get("table") or "")
        slot = out.setdefault(table, [])
        for c in (r.get("values") or {}):
            if c not in slot:
                slot.append(c)
    return out


def schema_sql(bp: Blueprint, extra_columns: dict[str, list[str]] | None = None) -> str:
    """蓝图 → CREATE TABLE。纯代码：确定、免费、绝不漏表。

    `extra_columns`：**种子里出现的列**。必须并进来，否则 `INSERT` 会因为
    "table X has no column named Y" **整体失败**，九张表一行数据都没有（实测踩过：
    种子 36 行全丢，判据于是全部卡在"列表里找不到那条记录"）。
    """
    lines = ["-- 由管线按结构设计生成（确定性，零 LLM）", ""]
    # 会话表由**管线**负责：登录/会话是基础设施，不是业务语义。
    # 实测踩过：蓝图里的 sessions 表列名与管线会话路由不一致（或根本没有这张表）→
    # 登录返回 200 但会话存不下来 → `/api/auth/me` 永远 401 → 前端永远显示"未登录"。
    lines.append("CREATE TABLE IF NOT EXISTS sessions (")
    lines.append("  session_id TEXT PRIMARY KEY,")
    lines.append("  user_id INTEGER,")
    lines.append("  created_at TEXT")
    lines.append(");")
    lines.append("")
    merged = _table_columns(bp)
    for tname, cols in (extra_columns or {}).items():
        slot = merged.setdefault(tname, [])
        for c in cols:
            if c not in slot:
                slot.append(c)
    bp_unique: dict[str, list[str]] = {}
    for t in bp.db_tables:
        tname = re.sub(r"[^A-Za-z0-9_]", "_", str(t.get("name") or "table"))
        uq = [re.sub(r"[^A-Za-z0-9_]", "_", str(u)) for u in (t.get("unique") or [])]
        bp_unique.setdefault(tname, []).extend([u for u in uq if u])
    for name, cols in merged.items():
        cols = _dedupe_cols(list(cols))
        if "id" not in cols:
            cols.insert(0, "id")
        decls = []
        for c in cols:
            # 🔴 **列名要加引号**：模型会用 `default` / `order` / `group` 这类 SQL 关键字当列名，
            #    不加引号 → `near "default": syntax error` → 整份 schema 执行中止（实测）。
            decls.append('  "id" INTEGER PRIMARY KEY AUTOINCREMENT' if c == "id" else f'  "{c}" TEXT')
        # ⚠️ **不再建 UNIQUE 约束**（2026-09-28 实测代价很大）：
        #    种子用 `INSERT OR IGNORE` 保证幂等，而 `OR IGNORE` **会连 UNIQUE 冲突一起吞掉** ——
        #    实测 `workbooks` 表里 `UNIQUE ("name")` + 两次种子运行 → 表**始终为空**、
        #    而 bootstrap 报告 0 错误、接口 200 返回 `[]`（判据"列表里找不到那条记录"）。
        #    失败模式是静默的、且只在运行期显现 → 宁可不要这个约束。
        lines.append(f'CREATE TABLE IF NOT EXISTS "{name}" (')
        lines.append(",\n".join(decls))
        lines.append(");")
        lines.append("")
    return "\n".join(lines)


def blueprint_digest(bp: Blueprint, *, max_routes: int = 24, max_eps: int = 40) -> str:
    out = ["路由：" + ", ".join(f"{r['path']} -> {r.get('component')}" for r in bp.routes[:max_routes])]
    out.append("端点：" + ", ".join(f"{e['method']} {e['path']}" for e in bp.api_endpoints[:max_eps]))
    if bp.db_tables:
        out.append("表：" + "; ".join(f"{t['name']}({ ', '.join(t.get('columns') or []) })"
                                      for t in bp.db_tables[:12]))
    if bp.auth:
        out.append("认证：" + bp.auth[:200])
    if bp.error_shape:
        out.append("错误体：" + bp.error_shape[:160])
    return "\n".join(out)


def file_context(job: FileJob, bp: Blueprint, groups: list[Group], existing: list[str]) -> str:
    """这个文件需要的**最小上下文**。"""
    parts: list[str] = []
    if job.req_ids:
        wanted = set(job.req_ids)
        specs = [s for g in groups for s in g.specs if s.req_id in wanted]
        parts.append("## 这个文件要实现的需求\n\n" + render_block(specs))
    if job.kind == "page":
        # 🔴 **字段族落点契约**（实测：`getByLabel(/Email address/i)` 找不到 → 6 条判据连登录都过不去）：
        #    字段类名字必须是"**有 label 关联的输入框**"（`<label htmlFor>` + `<input id>`），
        #    或者带同名 placeholder；只在文案里写一遍不算。
        fields = [t for t in (job.extra.get("field_targets") or [])]
        buttons = [t for t in (job.extra.get("button_targets") or [])]
        if buttons:
            parts.append("## 本页的**按钮**契约（判据逐个点名点它们，名字逐字）\n"
                         + "\n".join(f"- `{n}`：一个可点击的 <button>（名字逐字，不要加前缀/后缀）"
                                     for n in buttons))
        if fields:
            parts.append("## 本页的**字段**契约（判据用 getByLabel / getByPlaceholder 找，位置必须对）\n"
                         + "\n".join(
                             f"- `{n}`：渲染成一个输入控件，且**用 <label htmlFor=\"x\">` {n} </label> + <input id=\"x\" />** 关联"
                             for n in fields)
                         + "\n（密码框加 `type=\"password\"`；复选框用原生 `<input type=\"checkbox\">`。）")
    if job.kind == "api-router":
        parts.append("## 本文件的端点\n" + "\n".join(
            f"- {e['method']} {e['path']}  请求：{e.get('request', '')}  响应：{'; '.join(e.get('responses') or [])}"
            for e in job.extra.get("endpoints", [])))
    if job.kind == "page":
        routes = job.extra.get("routes") or [job.extra.get("route", {}).get("path")]
        parts.append("## 这个页面\n路由：" + ", ".join(str(r) for r in routes if r))
    parts.append("## 结构（全应用）\n" + blueprint_digest(bp))
    if existing:
        parts.append("## 本应用已有的文件\n" + "\n".join(f"- {p}" for p in sorted(existing)))
    return "\n\n".join(parts)


def strip_fences(text: str) -> str:
    """模型常把代码包在围栏里，也常带一句解释 —— 只取代码。"""
    t = text.strip()
    m = re.search(r"`{3}[a-zA-Z]*\n(.*?)`{3}", t, re.S)
    if m:
        return m.group(1).rstrip() + "\n"
    return t + "\n"


def _app_token(out_dir: Path, bp: Blueprint) -> str:
    """应用名（导航里的"回首页"控件用它）——从蓝图的路由组件或需求标题里取一个词。"""
    import json as _json
    # 应用名优先取需求包标题（`.arc/g2-plan.json` 的 pack 字段，由 compile 写入）。
    plan = Path(out_dir) / ".arc" / "g2-plan.json"
    if plan.is_file():
        try:
            pack = str((_json.loads(plan.read_text(encoding="utf-8")) or {}).get("pack") or "").strip()
            if pack:
                words = [w for w in pack.split() if w[:1].isalpha()]
                if words:
                    return words[0]
        except Exception:  # noqa: BLE001
            pass
    p = Path(out_dir) / ".arc" / "g2-blueprint.json"
    if p.is_file():
        try:
            data = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
        for key in ("app_name", "title", "name"):
            val = str(data.get(key) or "").strip()
            if val:
                return val.split()[0]
    return "Home"


def _read_json(out_dir: Path, name: str):
    p = Path(out_dir) / ".arc" / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def nav_contract(out_dir: Path) -> str:
    """**硬契约**：测试会点的名字、测试断言必须可见的文本、以及必须预置的记录。

    三条都是零 token 从测试与夹具里抽出来的**权威事实**，实测每一条都能整批救活判据：
      - 名字 + 角色不对 → `clickNamed` 找不到 → 测试卡在导航那一步（实测 30/32 条是这个形态）；
      - 断言文本不在页面上 → `expectTextsVisible` 直接失败；
      - 夹具记录不存在 → 列表页点不进详情页。
    """
    parts: list[str] = []
    items = _read_json(out_dir, "nav_targets.json") or []
    if items:
        def line(it: dict) -> str:
            name = it.get("name", "")
            role = it.get("role", "button")
            if it.get("source") == "clickNamed":
                # clickNamed 会依次试 button/link/tab/menuitem/text —— 给它**两种都做**，
                # 否则判据的第一候选落空、整条测试卡在导航那一步（实测 30/32 条是这个形态）。
                return (f"- \"{name}\"：**同一个名字要有 <button> 也要有 <a>/<Link>**"
                        "（判据会两种都试，缺一种就可能点不到）")
            return f"- \"{name}\"：需要一个 [{role}] 元素（名字逐字）"

        parts.append("## 测试会点的名字（**逐字照抄；元素类型也要对上，否则整条判据挂**）\n"
                     + "\n".join(line(it) for it in items)
                     + "\n（**登录之后**每一个页面都要能点到它们 —— 放在顶层布局的导航区里最稳；"
                     + "未登录时只给 Login 入口。）")
    lits = _read_json(out_dir, "assert_texts.json") or []
    if lits:
        parts.append("## 测试断言**必须可见**的文本（逐字，英文原文，不许翻译）\n"
                     + "\n".join(f"- \"{x}\"" for x in lits[:24])
                     + "\n（它们必须在页面上是**可见元素**；标题文本请用 heading 元素并逐字写这些字符串。）")
    return "\n\n".join(parts)


def visual_map(out_dir: Path, req_dir: Path | None = None,
               page_names: list[str] | None = None) -> dict[str, list[str]]:
    """把 `reference/*.png` 按**文件名与页面名的词重叠**配到页面上（零 token）。

    为什么要有它：官方需求带参考截图（一个 9 张、另一个 27 张），而 YAML 里**没有**
    `visual_reference` 字段 —— 唯一线索就是文件名（`create-workbook.png` → "CreateWorkbook"）。
    只按词重叠配，配不上就不配。
    """
    if req_dir is None:
        return {}
    ref = Path(req_dir) / "reference"
    if not ref.is_dir():
        return {}
    images = sorted(ref.glob("*.png"))
    if not images:
        return {}
    # ⚠️ **不能去 glob 目录**：本函数在 emit 里是**文件循环之前**调用的（那时页面还不存在）→
    #    实测某官方题配到 **0 个页面**。改成用**计划里的页面名**（由调用方传入 jobs）。
    _names = list(page_names or [])
    if not _names:
        _names = [p.name for p in sorted((Path(out_dir) / "frontend" / "src" / "pages").glob("*.tsx"))]
    out: dict[str, list[str]] = {}
    def _words(name: str) -> set[str]:
        # ⚠️ 必须**同时**按 CamelCase 与 kebab/snake 切：页面叫 `CreateWorkbookPage`
        #    （一个长词），图片叫 `create-workbook.png` → 不切 CamelCase 就一个都配不上（实测 0）。
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
        return {w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower())
                if len(w) > 2 and w not in {"page", "home", "view", "list", "detail"}}

    for page in [Path(n) for n in _names]:
        words = _words(page.stem)
        hits: list[str] = []
        for img in images:
            iw = _words(img.stem)
            if words & iw:
                hits.append(str(img))
        if hits:
            out[page.name] = hits[:2]
    return out


def _looks_truncated(text: str, finish_reason: str) -> bool:
    """判断输出是否被截断：finish_reason=length，或**成对符号不平衡**。

    为什么不只看 finish_reason：有些网关不返回它；而"反引号/大括号/引号落单"是截断的可靠指纹
    （正常完成的文件在 `strip_fences` 之后这些符号都是配平的）。
    """
    if finish_reason in {"length", "max_tokens"}:
        return True
    body = strip_fences(text)
    if not body.strip():
        return True
    pairs = [(body.count("{"), body.count("}")), (body.count("("), body.count(")")),
             (body.count("["), body.count("]"))]
    if any(a != b for a, b in pairs):
        return True
    # 反引号必须是偶数（成对）；奇数说明正好断在模板字符串中间
    return body.count("`") % 2 == 1

def write_file(cfg: LLMConfig, job: FileJob, bp: Blueprint, groups: list[Group], out_dir: Path,
               *, existing: list[str], ledger: Ledger | None = None, log=print) -> dict:
    target = Path(out_dir) / job.path
    target.parent.mkdir(parents=True, exist_ok=True)
    if job.kind == "plumbing":
        name = Path(job.path).name
        if name == "bootstrap.js":
            js = bootstrap_js()
        elif name == "_session.js":
            js = session_router_js()
        elif name == "index.js":
            js = _plumbing("index.js")
        elif name == "seed_db.js":
            js = _plumbing("seed_db.js")
        else:
            js = auth_js()
        target.write_text(js, encoding="utf-8")
        return {"path": job.path, "ok": True, "how": "deterministic", "chars": len(js)}
    if job.kind == "app-shell-deterministic":
        js = app_js(list(job.extra.get("routers") or []))
        target.write_text(js, encoding="utf-8")
        return {"path": job.path, "ok": True, "how": "deterministic", "chars": len(js)}
    if job.kind == "shell-tsx":
        nav = _read_json(out_dir, "nav_targets.json") or []
        token = _app_token(out_dir, bp)
        tsx = app_tsx(out_dir, list(job.extra.get("routes") or []), nav, token)
        target.write_text(tsx, encoding="utf-8")
        return {"path": job.path, "ok": True, "how": "deterministic", "chars": len(tsx)}
    if job.kind == "schema":
        sql = schema_sql(bp, _merge_columns(seed_columns(out_dir), sql_columns(out_dir)))
        target.write_text(sql, encoding="utf-8")
        return {"path": job.path, "ok": True, "how": "deterministic", "chars": len(sql)}
    rules = FRONTEND_RULES if job.path.startswith("frontend/") else BACKEND_RULES
    nav = nav_contract(out_dir) if job.path.startswith("frontend/") else ""
    ctx = file_context(job, bp, groups, existing)
    if job.path.startswith("frontend/"):
        # 🔴 给出**完整可达路径清单**（不是只给前缀）：实测只给前缀时，页面会自己拼出
        #    `/api/login/me`、`/api/auth/status` 这类不存在的路径 → 404 → 那一页废掉。
        #    清单来自后端**已生成的**路由声明（挂载前缀 + router 子路径），所以它一定准。
        try:
            from ..verify.staticcheck import _mounts_and_routes
            reachable = sorted(_mounts_and_routes(Path(out_dir)))
        except Exception:  # noqa: BLE001
            reachable = []
        if reachable:
            ctx = ("## 后端**真实存在**的 API 路径（只能调这些，逐字）\n"
                   + "\n".join(f"- {p}" for p in reachable)
                   + "\n（把 `:id` 这类段替换成真实 id；**不要发明新路径** —— 调不存在的路径会 404，\n"
                     "  登录/列表/详情就整页废掉。会话相关的用 `/api/auth/check` 与 `/api/auth/login`。）\n\n" + ctx)
    if nav:
        ctx = nav + "\n\n" + ctx
    note = ""
    if target.is_file():
        old = target.read_text(encoding="utf-8", errors="replace")
        if len(old) < 3500:
            note = "\n\n## 该文件现有内容（可整文件替换）\n" + old
    messages = [
        {"role": "system", "content": "你是资深全栈工程师。只输出**完整文件内容**，不要解释。\n"
                                     f"目标文件：{job.path}\n用途：{job.purpose}\n\n{rules}"},
        {"role": "user", "content": ctx + note},
    ]
    # ---- 参考截图（可选、默认关、失败即熔断）----
    # 官方需求带 `reference/*.png`；YAML 没有 `visual_reference` 字段，所以按文件名与页面名
    # 的词重叠配对（`visual_map`，零 token）。**默认不开**：本地实测网关对图片会直接断连，
    # 而平台是否给 `VISUAL_MODEL` 未知 → 只有明确给了视觉模型（或 G2_VISION=1）才走图，
    # 且一次失败就全局熔断，绝不拖垮整轮生成。
    images: list[str] = []
    try:
        from . import llm as _llm
        _vstate = getattr(_llm, "VISION_OK", None)
        _vleft = getattr(_llm, "VISION_MAX", 0) - getattr(_llm, "VISION_USED", 0)
        if (os.environ.get("G2_VISION") != "0" and _vstate is not False and _vleft > 0
                and not getattr(_llm, "VISION_DISABLED", False)):
            vmap = _read_json(out_dir, "visual_map.json") or {}
            images = list(vmap.get(Path(job.path).name) or [])[:2]
            if images:
                parts = [{"type": "text", "text": str(messages[-1].get("content") or "")}]
                for ip in images:
                    try:
                        b64 = base64.b64encode(Path(ip).read_bytes()).decode()
                    except OSError:
                        continue
                    parts.append({"type": "image_url",
                                  "image_url": {"url": "data:image/png;base64," + b64}})
                if len(parts) > 1:
                    messages = messages[:-1] + [{"role": "user", "content": parts}]
                    _llm.VISION_USED = getattr(_llm, "VISION_USED", 0) + 1
                    log(f"      （附上 {len(parts) - 1} 张参考截图，本run第 {_llm.VISION_USED}/"
                        f"{getattr(_llm, 'VISION_MAX', 0)} 次）")
                else:
                    images = []
    except Exception:  # noqa: BLE001
        images = []
    messages = fit(messages, priorities=[0, 1], log=log)
    size = message_chars(messages)
    log(f"    -> {job.path}（请求 {size} 字符）")
    try:
        text = chat(cfg, messages, stage=f"write:{job.kind}", node_id=job.path, ledger=ledger, log=log)
        if images:
            from . import llm as _llm_ok
            if getattr(_llm_ok, "VISION_OK", None) is None:
                _llm_ok.VISION_OK = True
                log("      ✅ 视觉通路可用（后面继续带图）")
    except Exception as exc:  # noqa: BLE001
        if not images:
            raise
        # 读图这条链路一旦出问题，**立刻熔断并退回纯文本**（平台那次跑不能因此挂掉）
        from . import llm as _llm2
        _llm2.VISION_OK = False
        _llm2.VISION_DISABLED = True
        log(f"      ⚠️ 带图调用失败（{type(exc).__name__}）→ 关闭视觉，改用纯文本重试")
        messages = [m for m in messages if not isinstance(m.get("content"), list)]
        text = chat(cfg, messages, stage=f"write:{job.kind}", node_id=job.path, ledger=ledger, log=log)
    # 🔴 **截断就重问一次精简版**：`finish_reason == "length"` 或产物明显不完整（反引号/大括号不平衡）
    #    时，同一份 prompt 再要一次没意义（还会再被截断），必须**换要求**：更短、更少样式。
    finish = getattr(chat, "last_finish_reason", "")
    if _looks_truncated(text, finish):
        log(f"      ⚠️ 输出疑似被截断（finish_reason={finish or '?'}）→ 重问精简版")
        slim = [dict(m) for m in messages]
        slim[0] = dict(slim[0])
        slim[0]["content"] = (str(slim[0].get("content") or "")
                              + "\n\n## 上一次回答被长度截断了\n"
                                "请重新输出**完整但精简**的文件：**不超过 250 行**；\n"
                                "去掉多余样式、注释与重复逻辑；**不要省略任何接线**"
                                "（路由/挂载/import/渲染都要在）；字符串一律用单引号或双引号拼接，**不要用反引号**。")
        try:
            text = chat(cfg, slim, stage=f"write:{job.kind}", node_id=job.path + ":slim",
                        ledger=ledger, log=log)
        except Exception as exc:  # noqa: BLE001
            log(f"      （精简重问失败，用原输出：{type(exc).__name__}）")
    code = strip_fences(text)
    # 🔴 落盘前把模板字符串改写成拼接：反引号在长文件里会被截断 → 构建失败 → 整轮作废。
    #    （规则+判据都拦不住模型，那就**确定性改写**。）
    code, fixed = convert_templates(code)
    if fixed:
        log(f"      （已把 {fixed} 处模板字符串改写为拼接）")
    # 🔴 **必须真的落盘**：此前一次编辑把这一行弄丢了，于是"日志说写了、磁盘上没有"——
    #    产物里只剩模板文件，后端 `Cannot find module`，而所有静态判据都是绿的（很难发现）。
    target.write_text(code, encoding="utf-8")
    return {"path": job.path, "ok": True, "how": "llm", "chars": len(code), "request_chars": size}