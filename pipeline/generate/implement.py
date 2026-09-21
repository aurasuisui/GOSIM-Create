"""实现生成（管线第 2/4 阶段的最小版本）。

**M3a 的存在理由**：`pipeline/` 此前一次 LLM 调用都没有过——榜单上的 0/86
只测量了"一个只有骨架的应用"，**不测量生成能力**。这个模块是第一次真的生成功能。

三调用结构（与 `PLAN.md` §3.3 的六阶段对应，但压到最小）：

    1. 设计   —— 小输出：路由 / 组件 / 接口 / 表结构。让后端与前端两份实现**对齐接口**
    2. 后端   —— Express 路由 + 服务 + 仓储（复用模板的 database/ 脚手架）
    3. 前端   —— React 页面 + api 客户端（复用模板的 axios 实例与路由）

**输出格式用分隔符而不是 JSON**：多文件代码放进 JSON 字符串要模型自己转义换行/引号，
失败率高且白烧 token。这里用

    ===FILE: frontend/src/App.tsx===
    ```tsx
    ...
    ```

`parse_files()` 按 `===FILE:` 行切分，取紧随其后的第一个 fenced block。

**安全边界**：只允许写 `frontend/` 与 `backend/` 下的文件，且**禁止覆盖
`backend/src/database/`**（那是平台/模板提供的数据库脚手架，`PLAN.md` §5 明确要求复用而非重建）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .llm import LLMConfig, chat, totals
from .schema import SCHEMA_REL, inject_schema

# ---- 技术栈契约：改编自 ARC 的 app_type_handler/web.py:1060-1095（官方原文，只删掉与本次无关的测试小节）----
STACK_CONTRACT = """### Main Stack
- backend: nodejs (Express)
- frontend: react (Vite)
- database: sqlite (sqlite3 driver, file-based)

### Runtime And Hosting
* **Single Web Port**: the Express backend serves the built frontend on the SAME origin.
* **Hosting Model**: `frontend` builds to `frontend/dist` (via `npm run build`); `backend` starts with
  `npm run start` and must serve `frontend/dist` (the template already wires this up).
* **Deployment Rule**: never rely on a separate frontend dev server.

### Frontend rules
* React 18+ with `react-router-dom` (`BrowserRouter` is already set up in `frontend/src/main.tsx`).
* TypeScript + TSX for all frontend source files.
* HTTP via the existing axios instance at `frontend/src/api/index.ts` (its baseURL is `/api`).
* **Only use dependencies that are already in `frontend/package.json`**:
  react, react-dom, react-router-dom, axios, jsdom. Do NOT import anything else.

### 定位契约（**生成时必须遵守**；外部测试只用这三种方式找元素）
外部测试只能通过 **`getByLabel(名字)` / `getByRole(<role>, {name})` / `getByText(文本)`** 定位与断言
（禁用 class/id/data- 选择器与直连 API）。所以：

* **每个控件都要有原生 label 关联**：`<label htmlFor="x">…</label>` + `<input id="x">`，
  不要只靠 placeholder / class / DOM 顺序。
* **实体值（用户名、标题、单号…）必须渲染在它自己的元素里**，例如 `<span>{user.username}</span>`。
  **绝不能把它拼进句子里**（`Welcome, {user.username}!` 这种形态会让
  `getByText(值, {exact: true})` **永远匹配不到**）——实测这是最常掉的几条。
* **导航/退出入口用真正的链接**（`<a>` / `<Link>`），不要用 `<button>` 冒充链接
  （判据按 `getByRole('link', …)` 找）。
* **错误消息渲染在 `role="alert"` 的元素里**，且内容非空、可见。
* **下拉用原生 `<select>`**：初始占位项写成 `<option value="">需求里的提示语</option>`
  ——**文本用提示语，value 必须留空串**（外部测试直接断言 `toHaveValue('')`；
  把 value 写成提示语会当场判失败）。默认值另有要求的（如国家区号 `+86`）按需求给。
  **密码强度用 `<meter>`** 并设置 `aria-valuenow`（1/2/3 三档，随输入变化）；
  ⚠️ **`<meter>` 还必须额外写 `aria-label`**（如 `aria-label="密码强度"`）：
  只靠 `<label htmlFor>` 时**它的 ARIA 可访问名是空的**——`getByLabel` 找得到，
  但 `getByRole('meter', {name: /密码强度/})` **找不到**（实测：这是外部判据唯一挂掉的那条）。
  `<label>` 照常写，**两者都要**。**复选框用原生 `<input type="checkbox">`**。

### Backend rules
* Express with `body-parser` + `cors` (already wired in `backend/src/app.js`).
* ⚠️ **后端是 CommonJS 工程**（`backend/package.json` 没有 `"type": "module"`）：
  一律用 `require(...)` / `module.exports = {...}`，**不要写 `import` / `export`**。
  脚手架的 `./database` 是 `module.exports`，用 `import { run, get }` 会直接
  `SyntaxError: does not provide an export named 'get'` → **后端进程起不来**（判据全挂）。
  前端的 `.tsx` 才用 ESM。
* **Reuse the provided database scaffold** — import it from `./database` (barrel export at
  `backend/src/database/index.js`). It gives you:
  `run(sql, params)` / `get(sql, params)` / `all(sql, params)` / `exec(sql)` / `withTransaction(work)`,
  plus `initializeDatabase()` / `seedDatabase()`.
  **Do NOT create your own sqlite connection.**
  **建表只写在 `backend/src/database/schema.sql`（纯 SQL）**——管线会把它注入到
  `initializeDatabase()` 的启动流程里（`PRAGMA` 之后、`return database;` 之前）并 await 执行。
  你**不需要、也不允许**为了建表去改任何 `.js`；`backend/src/database/` 下的文件一律逐字节不动
  （写 `init_db.js` 会被直接拒绝）。
* Routes live under `/api/...`. `backend/src/app.js` already has the mount points marked with
  comments (`// route modules imports` and `// register routes`).
* Never return password hashes or raw credentials in any API response."""


# `design` 会**每一块都重发**，所以给它一个上限（实测：不限时把请求顶到 19,410 字符 → 整个 run 作废）
DESIGN_IN_PROMPT_MAX = int(os.environ.get("PIPELINE_DESIGN_MAX_CHARS") or 4000)


class GenerationError(RuntimeError):
    """生成失败——必须报出来，不能产出一个"看起来成功但什么都没写"的结果。"""


# ---------------------------------------------------------------- brief 组装

def build_skeleton_brief(output_dir: Path, log=print) -> str:
    """把模板骨架的现状讲给模型听：文件清单 + 关键接入点的原文。"""
    lines: list[str] = []
    for top in ("frontend/src", "backend/src"):
        base = output_dir / top
        if not base.is_dir():
            continue
        lines.append(f"#### existing files under `{top}/`")
        for f in sorted(base.rglob("*")):
            if f.is_file():
                lines.append(f"  - {f.relative_to(output_dir).as_posix()}")
        lines.append("")

    def show(rel: str, limit: int = 4000) -> str:
        f = output_dir / rel
        if not f.is_file():
            return f"(missing: {rel})"
        text = f.read_text(encoding="utf-8", errors="replace")
        if len(text) > limit:
            text = text[:limit] + "\n…(truncated)"
        return f"#### `{rel}`\n```\n{text}\n```"

    # 只展示"必须扩展/对接"的接线点，不贴整个模板
    for rel in ("backend/src/app.js", "backend/src/index.js",
                "backend/src/database/index.js",
                "frontend/src/App.tsx", "frontend/src/main.tsx",
                "frontend/src/api/index.ts"):
        lines.append(show(rel))
    brief = "\n".join(lines)
    log(f"  骨架 brief: {len(brief)} 字符")
    return brief


def build_requirement_brief(tree, a11y_index, req_ids: list[str] | None) -> str:
    """把目标需求写成 brief：描述 + 场景 + 可访问名清单（后者是我们抽出来的**靶子**）。"""
    nodes = [n for n in tree.ordered() if n.is_leaf and n.scenarios]
    if req_ids:
        wanted = set(req_ids)
        nodes = [n for n in nodes if n.id in wanted]
    if not nodes:
        raise GenerationError("没有可生成的需求节点（检查 req_ids / 需求树）")

    parts: list[str] = []
    by_req = a11y_index.by_req()
    for n in nodes:
        parts.append(f"=== 需求 {n.id}: {n.name} ===")
        if n.description:
            parts.append(n.description.strip())
        for sc in n.scenarios:
            parts.append(f"--- 场景: {sc.name} ---")
            for st in sc.steps:
                parts.append(f"{st.keyword}: {(st.content or '').strip()}")
            parts.append("")
        names = by_req.get(n.id) or []
        if names:
            parts.append("必须提供的可访问名（Playwright 会用 getByLabel / getByRole(name) 找它们，"
                         "控件必须与 label 原生关联，不得只靠 placeholder / class / DOM 顺序）：")
            for e in names:
                role = e.role or "(role 未识别)"
                opt = f"，选项：{e.options}" if e.options else ""
                parts.append(f"  - [{role}] {e.name!r}{opt}")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------- 三次调用

def _design_prompt(requirement_brief: str) -> list[dict]:
    return [
        {"role": "system", "content":
            "你是资深全栈工程师，正在为一个需求做**极简设计**。只输出 JSON，不要任何解释文字。"},
        {"role": "user", "content": f"""为下面的需求做设计。技术栈见后。

{STACK_CONTRACT}

## 需求

{requirement_brief}

## 输出

只输出这一段 JSON（键名照抄，值按你的设计填）：

{{
  "routes": [{{"path": "/register", "component": "RegisterPage", "purpose": "一句"}}],
  "api_endpoints": [{{"method": "POST", "path": "/api/auth/register",
                      "request": "字段名列表", "responses": ["201 + 字段", "400 + error 字段"]}}],
  "db_tables": [{{"name": "users", "columns": ["id", "username", "..."], "unique": ["username"]}}],
  "session": "一句话说明会话怎么建、怎么在刷新后保持",
  "error_shape": "错误响应体的 JSON 形状，前端按它渲染可见错误"
}}

要求：
- 路由必须覆盖需求里出现的每一个路径（例如测试会直接点击 `a[href="/register"]`）。
- `api_endpoints` 要够前端把三种场景都做完（成功 / 校验失败 / 重复冲突）。"""},
    ]


# ---------------------------------------------------------------- 分块生成
# ⚠️ 为什么分块：实测**一次要太多文件会让网关断开连接**
# （设计那一次 9051 输出 token 里有 8413 是 reasoning；把 5 个文件的实现压在一次里会更大）。
# 分块之后每次只要 1–2 个文件，输出可控；代价是 brief 重发几次（input token 变多，但便宜）。

CHUNKS: list[dict] = [
    {
        "name": "backend-schema",
        "needs_skeleton": False,
        "side": "backend",
        "role": "你是资深数据库工程师。",
        "files": ["backend/src/database/schema.sql"],
        "ask": """输出**建表脚本**（一个纯 SQL 文件；不要任何 JS、不要解释文字）：
- 每条语句以 `;` 结尾，一律 `CREATE TABLE IF NOT EXISTS`（幂等，可重复执行）。
- 覆盖设计里 `db_tables` 的**所有**表，**外加会话表**（会话必须落库，重启后仍有效）。
- 只输出 SQL：不要 `BEGIN`/`COMMIT`、不要 `PRAGMA`、不要 `INSERT`、不要 `ALTER`。
- 列名与设计一致；唯一约束写 `UNIQUE(...)`；关联写 `REFERENCES 表(列)`；时间列给默认值。
- 密码只存摘要，列名用 `password_hash`——**不要**出现明文密码列。
- 这是整个应用**唯一**的建表位置（由后端在启动时执行）：
  应用用到的每一个表都必须在这里出现，一个都不能少。""",
    },
    {
        # ⚠️ 拆成一文件一调用（2026-09-21 闸门第 0 条实测）：原来"仓储 + 服务"合成一块，
        # 请求 12,974 字符、输出 ~2,800 token —— 比赛网关上**连续两次 RemoteDisconnected**，
        # 第三次才勉强过，整轮 run 的时间都耗在这里。同一模型在 M3a 就是这个形态
        # （输出越大越容易被断），所以按"一文件一调用"切细：每次输出更短、更快结束。
        "name": "backend-auth-repo",
        "needs_skeleton": False,
        "side": "backend",
        "role": "你是资深 Node.js 后端工程师。",
        "files": ["backend/src/auth/auth_repository.js"],
        "ask": """实现**账号仓储**这一个文件：
用脚手架 `./database` 导出的 `run/get/all/withTransaction` 做读写；
表已经由 `backend/src/database/schema.sql` 定义好（本块之前产出，原文随本块附上，**列名照它写**）。
**不要在任何 JS 里写 `CREATE TABLE`**，也不要改 `backend/src/database/` 下的任何文件。
仓储模块只负责查询与写入：按用户名/邮箱查询；插入账号；建会话 / 查会话 / 删会话；
**存密码摘要而不是明文**（用 Node 内置 crypto）。导出清晰的命名函数，供上层 service 调用。""",
    },
    {
        "name": "backend-auth-service",
        "needs_skeleton": False,
        "side": "backend",
        "role": "你是资深 Node.js 后端工程师。",
        "files": ["backend/src/auth/auth_service.js"],
        "ask": """实现**账号业务服务**这一个文件：
- **全部服务端校验**逐条实现需求里列出的规则：3–32 位用户名字符集、12–128 位密码且含大小写+数字+特殊字符、
  两次密码一致、证件类型/优惠类型必须来自白名单、手机号必须与国家区号组合通过校验、
  姓名去空格后 2–100 个非空白字符、证件号码 6–30 位 ASCII 字母数字连字符、
  邮箱可选但填写时必须合法且 ≤254 字符、协议必须勾选。
- 重复用户名 / 重复邮箱（忽略大小写）要能报出来（唯一键冲突也要转成同一类错误）。
- 校验失败**抛带中文 message 的错误**（消息要能让前端直接显示）。
- 成功时用 crypto 生成会话令牌并调用仓储落库，**绝不返回密码或摘要**。
- 仓储的**确切函数名**见随本块附上的 `auth_repository.js` 原文 —— 调它们，不要自己写 SQL。""",
    },
    {
        "name": "backend-routes",
        "needs_skeleton": True,
        "side": "backend",
        "role": "你是资深 Node.js 后端工程师。",
        "files": ["backend/src/routes/auth_routes.js", "backend/src/app.js"],
        "ask": """把 HTTP 层接起来：
- `auth_routes.js`：注册（POST）、当前登录态（GET）、登出（POST 或 GET）三个端点；
  注册成功时用 **httpOnly cookie** 建立会话；错误统一返回 JSON（含中文 message）。
  会话的存储也要落库（用脚手架；表已经由 `schema.sql` 定义好，列名照它写），这样重启后仍然有效。
- `app.js`：**输出完整文件**，把上面的路由挂上去（原文件里 `// route modules imports` 与
  `// register routes` 就是接入点），**保留原有静态托管与 SPA fallback 逻辑**，
  并加上 cookie 解析（不要引入新依赖，自己解析 `req.headers.cookie`）。""",
    },
    {
        "name": "frontend-register",
        "needs_skeleton": True,
        "side": "frontend",
        "role": "你是资深 React + TypeScript 前端工程师。",
        "files": ["frontend/src/features/registration/RegisterPage.tsx"],
        "ask": """实现**注册页**这一个组件（这是验收的核心）：
- 用 `<label htmlFor>` + `id` 把每个控件的可访问名做成需求里给的中文名字（**逐字照抄需求里的名字**）。
- 证件类型 / 优惠（待）类型 / 国家/地区代码 用**原生 `<select>`**：前两个初始 value 必须为空串，
  国家/地区代码默认 `+86` 且至少含 `+84`；选项文字用需求里给的选项（护照、成人 等）。
- 登录密码输入时，用**原生 `<meter>`** 显示密码强度（`aria-valuenow` 取 1/2/3 三档），并给它一个可访问名。
- 协议复选框的可访问名要包含需求里给的字样；提交按钮的可访问名等于需求里给的名字。
- 错误消息渲染在 `role="alert"` 的元素里（非空、可见、中文），来自后端返回的 message。
- 提交成功由上层负责跳转，本组件只调用传入的 `onRegistered` 回调（props 里给）。
- 只用 react / react-router-dom / axios（走 `frontend/src/api/index.ts` 的实例）。""",
    },
    {
        # 同样拆细（一文件一调用的理由见 backend-auth-repo 的注释）
        "name": "frontend-auth-client",
        "needs_skeleton": True,
        "side": "frontend",
        "role": "你是资深 React + TypeScript 前端工程师。",
        "files": ["frontend/src/api/auth.ts", "frontend/src/features/auth/AuthContext.tsx"],
        "ask": """实现**前端认证客户端**这两个文件：
- `api/auth.ts`：封装调用后端接口（用 `frontend/src/api/index.ts` 的 axios 实例，baseURL 已是 `/api`）。
  接口路径与请求/响应字段**照随本块附上的后端路由原文**写，不要自己另设路径。
- `AuthContext.tsx`：登录态（挂载时调当前登录态接口）、注册、登出；
  **不要用 localStorage**（会话在 httpOnly cookie 里，靠 cookie 重新拉取）。
  导出 `useAuth()`，后续页面靠它读写登录态。""",
    },
    {
        "name": "frontend-shell",
        "needs_skeleton": True,
        "side": "frontend",
        "role": "你是资深 React + TypeScript 前端工程师。",
        "files": ["frontend/src/pages/HomePage.tsx", "frontend/src/App.tsx"],
        "ask": """把应用外壳接起来：
- `HomePage.tsx`：未登录时首页要有**一个可点击的注册入口**（测试会点击 `a[href="/register"]`，
  所以这里要渲染一个 `href="/register"` 的链接）；已登录时**把当前用户名渲染在它自己的元素里**
  （`<span>{user.username}</span>`——不要拼进 `Welcome, {name}!` 这类句子里，
  外部测试按精确文本找这个值），以及一个可访问名含 `退出登录`（或 `Sign out`）的**链接**用于登出
  （必须是 `role=link`，不要用 button 冒充）。
- `App.tsx`：**输出完整文件**，用 `AuthProvider` 包住，并在 `<Routes>` 里加上 `/register` 路由。
- 登录态一律走随本块附上的 `AuthContext.tsx`（别自己再存一份）。""",
    },
]


def _clip(text: str, limit: int, what: str, *, log=print) -> str:
    """把**每次都重发**的长文本截到上限。

    为什么必须截（2026-09-21 闸门第 0 条第四次跑实测）：`design` 与骨架 brief 是**每一块都重发**的，
    而 flash 的设计输出很啰嗦。那次 `backend-auth-service` 的请求因此涨到 **19,410 字符（81% 预算）**
    → 网关 `RemoteDisconnected` → 被 `RequestTooLarge` 拦下、**整个 run 作废**。
    设计只是"照它实现"的摘要，截断的代价远小于整轮失败的代价。
    """
    if len(text) <= limit:
        return text
    log(f"  ⚠️  {what} {len(text)} 字符 → 截到 {limit}（每块都要重发，太大就会被网关丢）")
    return text[:limit] + "\n…(已截断；按上面这些实现即可，不要猜被截掉的内容)"


def _chunk_messages(chunk: dict, requirement_brief: str, design: str, skeleton: str,
                    written: list[str], side_extra: str, *, log=print) -> list[dict]:
    written_block = "\n".join(f"  - {w}" for w in written) if written else "  （还没有）"
    parts = [STACK_CONTRACT, ""]
    parts += ["## 设计（已定稿，照它实现）", "", _clip(design, DESIGN_IN_PROMPT_MAX, "design", log=log), ""]
    # 骨架 brief（模板文件原文）只有"要接线"的块才需要；纯新增文件的块带上它是白占预算
    if chunk.get("needs_skeleton"):
        parts += ["## 现有骨架", "", skeleton, ""]
    if side_extra:
        parts += [side_extra, ""]
    parts += ["## 需求", "", requirement_brief, "",
              "## 本次任务（只做这些文件，不要越界）", "",
              "必须要输出的文件：", "\n".join("  - " + f for f in chunk["files"]), "",
              "已经写好的文件（可 import / 遵循它们的约定）：", written_block, "",
              chunk["ask"], "",
              "## 输出格式（严格遵守）", "",
              "每个文件一段，路径从项目根算起：", "",
              f"===FILE: {chunk['files'][0]}===", "```js", "<完整文件内容>", "```", "",
              "不要输出任何其他文字。"]
    return [
        {"role": "system", "content": chunk["role"] + "只输出文件，不要解释。严格遵守给出的文件格式与边界。"},
        {"role": "user", "content": "\n".join(parts)},
    ]


# ---------------------------------------------------------------- 解析与落盘

RE_FILE = re.compile(r"^===FILE:\s*(\S+?)\s*===\s*$", re.M)
RE_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)\n```", re.S)

# 模型偶尔把**工具调用外壳**写进代码块。干跑实测（2026-09-21）：
# `RegisterPage.tsx` 末尾多了一行 `</parameter>` → esbuild 报
# `Expected identifier but found "/"`（443:1）→ **整轮构建失败 → 平台判 0**。
# 只删「整行、且标签名来自已知外壳集合」的行：正常 JSX 的 `</div>` / `<Route>` 不在此列，
# 所以这条清理不会碰到真代码。
RE_TOOL_JUNK_LINE = re.compile(
    r"^\s*</?(?:parameter|parameters|function_calls|invoke|tool_calls|tool_call|"
    r"tool_use|result|output|antml:[\w:]+)(?:\s[^>]*)?>\s*$", re.I | re.M)


def strip_tool_junk(content: str) -> str:
    return RE_TOOL_JUNK_LINE.sub("", content)


def parse_files(text: str) -> dict[str, str]:
    """把 `===FILE: path===` + fenced block 的回复解析成 {路径: 内容}。"""
    out: dict[str, str] = {}
    marks = list(RE_FILE.finditer(text))
    for i, m in enumerate(marks):
        path = m.group(1)
        body_start = m.end()
        body_end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[body_start:body_end]
        fence = RE_FENCE.search(body)
        content = fence.group(1) if fence else body.strip()
        content = strip_tool_junk(content)
        if content.strip():
            out[path] = content if content.endswith("\n") else content + "\n"
    return out


# ⚠️ **`init_db.js` 现在也是管线所有**：建表由 `generate/schema.py` 从 `schema.sql` 注入
# （模型改 JS 控制流找不对位置 → 注入到 `return database;` 之后成死代码，
#  两组 flash 产物各挂 3/6 就是这个形态）。其余脚手架文件是被复用的基础设施，逐字节不动。
FORBIDDEN_PREFIXES = (
    "backend/src/database/init_db.js",
    "backend/src/database/db_runtime.js",
    "backend/src/database/test_harness.js",
    "backend/src/database/seed_db.js",
    "backend/src/database/prepare_e2e.js",
    "backend/src/database/index.js",
)


def write_files(output_dir: Path, files: dict[str, str], log=print) -> list[str]:
    """把生成的文件写进输出目录，带路径安全检查。返回实际写入的相对路径列表。"""
    written: list[str] = []
    for rel, content in files.items():
        rel = rel.strip().lstrip("./")
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise GenerationError(f"拒绝写入越界路径：{rel}")
        if not (rel.startswith("frontend/") or rel.startswith("backend/")):
            raise GenerationError(f"拒绝写入 frontend/ 与 backend/ 之外的路径：{rel}")
        if rel in FORBIDDEN_PREFIXES:
            raise GenerationError(
                f"拒绝覆盖数据库脚手架：{rel}（建表请写 {SCHEMA_REL}，管线负责注入到启动流程）"
            )
        target = output_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    log(f"  写入 {len(written)} 个文件")
    return written


# ---------------------------------------------------------------- 主入口

def generate_app(tree, a11y_index, output_dir: Path, cfg: LLMConfig, *,
                 req_ids: list[str] | None = None, log=print) -> dict:
    """跑完整生成（1 次设计 + 逐块实现），落盘文件，返回摘要（含 token 用量）。"""
    requirement_brief = build_requirement_brief(tree, a11y_index, req_ids)
    skeleton = build_skeleton_brief(output_dir, log=log)
    log(f"  需求 brief: {len(requirement_brief)} 字符")

    design_path = output_dir / ".arc" / "design.json"
    if design_path.is_file() and not os.environ.get("PIPELINE_REGEN_DESIGN"):
        design = design_path.read_text(encoding="utf-8")
        log(f"  [1/{len(CHUNKS)+1}] 设计… 复用已有 {design_path.name}（{len(design)} 字符）")
    else:
        log(f"  [1/{len(CHUNKS)+1}] 设计…")
        design = chat(cfg, _design_prompt(requirement_brief), stage="design", log=log)
        design_path.parent.mkdir(parents=True, exist_ok=True)
        design_path.write_text(design, encoding="utf-8")
        log(f"        设计 {len(design)} 字符 → {design_path.name}")

    written: list[str] = []
    written_by_chunk: dict[str, list[str]] = {}
    for i, chunk in enumerate(CHUNKS, start=2):
        log(f"  [{i}/{len(CHUNKS)+1}] {chunk['name']}：{'、'.join(chunk['files'])}")
        extra = _side_extra(chunk, output_dir, written, log=log)
        text = chat(cfg, _chunk_messages(chunk, requirement_brief, design, skeleton, written, extra,
                                                 log=log),
                    stage=f"implement-{chunk['name']}", log=log)
        files = parse_files(text)
        if not files:
            raise GenerationError(f"{chunk['name']} 没有产出任何文件（回复里没有 ===FILE: 标记）")
        got = write_files(output_dir, files, log=log)
        written.extend(got)
        written_by_chunk[chunk["name"]] = got
        log(f"        -> {', '.join(got)}")

    usage = totals(cfg)
    log(f"  生成完成：{len(written)} 个文件，token {usage['total_tokens']}"
        f"（in {usage['input_tokens']} / out {usage['output_tokens']}）")

    # ---- 建表落位：把 schema.sql 注入到启动流程（PRAGMA 之后、return database; 之前）----
    # 这一步是**管线的责任**，不是模型的：模型追加到块末尾就会落到 `return database;`
    # 之后成死代码（两组 flash 产物各挂 3/6 就是这个形态）。详见 generate/schema.py。
    injected = inject_schema(output_dir, log=log)

    return {"files": written, "files_by_chunk": written_by_chunk,
            "schema_injection": injected,
            "usage": usage, "calls": list(cfg.calls)}


def _side_extra(chunk: dict, output_dir: Path, written: list[str], *, log=print) -> str:
    """给这一块补一段「上游产物的原文」，让两边的接口契约逐字对齐。

    ⚠️ **只带上这一块真正要调用的上游文件**（闸门第 0 条实测：把上游全塞进去会把请求
    顶到 13k 字符，比赛网关会断连）。所以：仓储块只要 schema；服务块要 schema + 仓储；
    路由块要 schema + 服务；前端块要路由。
    """
    name = chunk.get("name", "")
    if chunk.get("side") == "frontend":
        want = [w for w in written if w.startswith("backend/src/routes/")]
        # 外壳块要照着前面写好的认证客户端接（否则会自己另起一套登录态）
        want += [w for w in written if w.startswith(("frontend/src/features/auth/",
                                                     "frontend/src/api/"))]
    elif name == "backend-auth-service":
        want = [w for w in written if w.startswith("backend/src/auth/auth_repo")]
    elif name == "backend-routes":
        want = [w for w in written if w.startswith("backend/src/auth/auth_service")]
    else:
        want = []
    # 建表脚本是**每个后端块**的接口契约（列名必须对齐），一并附上原文
    if chunk.get("side") == "backend" and name != "backend-schema" and SCHEMA_REL in written:
        want = [SCHEMA_REL, *want]
    blocks = []
    budget = 7000          # 上游原文总量上限（把请求顶大就会被网关断连）
    for rel in want:
        f = output_dir / rel
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        room = budget - sum(len(b) for b in blocks)
        if room < 600:
            log(f"        （上游原文已达 {budget} 字符上限，{rel} 不再附上）")
            break
        if len(text) > min(6000, room):
            text = text[:min(6000, room)] + "...(truncated)"
        blocks.append("### 上游已写好的文件 `" + rel + "`（照它的接口写，不要改它）\n```\n" + text + "\n```")
    if blocks:
        log(f"        （随本块带上 {len(blocks)} 个上游文件原文，共 "
            f"{sum(len(b) for b in blocks)} 字符）")
    return "\n\n".join(blocks)
