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

### Backend rules
* Express with `body-parser` + `cors` (already wired in `backend/src/app.js`).
* **Reuse the provided database scaffold** — import it from `./database` (barrel export at
  `backend/src/database/index.js`). It gives you:
  `run(sql, params)` / `get(sql, params)` / `all(sql, params)` / `exec(sql)` / `withTransaction(work)`,
  plus `initializeDatabase()` / `seedDatabase()`.
  **Do NOT create your own sqlite connection, and do NOT modify anything under `backend/src/database/`.**
* Routes live under `/api/...`. `backend/src/app.js` already has the mount points marked with
  comments (`// route modules imports` and `// register routes`).
* Never return password hashes or raw credentials in any API response."""


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
        "name": "backend-auth",
        "side": "backend",
        "role": "你是资深 Node.js 后端工程师。",
        "files": ["backend/src/auth/auth_repository.js", "backend/src/auth/auth_service.js"],
        "ask": """实现**账号仓储与业务服务**这两个文件：
- `auth_repository.js`：用脚手架 `./database` 导出的 `run/get/all/withTransaction` 做读写；
  建表（users 表，含唯一约束）；按用户名/邮箱查询；插入账号；**存密码摘要而不是明文**（可用 Node 内置 crypto 做 scrypt/sha256）。
- `auth_service.js`：**全部服务端校验**（逐条实现需求里列出的规则，包括 3–32 位用户名字符集、
  12–128 位密码且含大小写+数字+特殊字符、两次密码一致、证件类型/优惠类型必须来自白名单、
  手机号必须与国家区号组合通过校验、姓名去空格后 2–100 个非空白字符、证件号码 6–30 位 ASCII 字母数字连字符、
  邮箱可选但填写时必须合法且 ≤254 字符、协议必须勾选）；重复用户名 / 重复邮箱（忽略大小写）要能报出来。
  校验失败**抛出一个带中文 message 的错误**（消息要能让前端直接显示）。
  成功时用 crypto 生成服务端账号标识与会话令牌，**绝不返回密码或摘要**。""",
    },
    {
        "name": "backend-routes",
        "side": "backend",
        "role": "你是资深 Node.js 后端工程师。",
        "files": ["backend/src/routes/auth_routes.js", "backend/src/app.js"],
        "ask": """把 HTTP 层接起来：
- `auth_routes.js`：注册（POST）、当前登录态（GET）、登出（POST 或 GET）三个端点；
  注册成功时用 **httpOnly cookie** 建立会话；错误统一返回 JSON（含中文 message）。
  会话的存储也要落库（用脚手架），这样重启后仍然有效。
- `app.js`：**输出完整文件**，把上面的路由挂上去（原文件里 `// route modules imports` 与
  `// register routes` 就是接入点），**保留原有静态托管与 SPA fallback 逻辑**，
  并加上 cookie 解析（不要引入新依赖，自己解析 `req.headers.cookie`）。""",
    },
    {
        "name": "frontend-register",
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
        "name": "frontend-shell",
        "side": "frontend",
        "role": "你是资深 React + TypeScript 前端工程师。",
        "files": ["frontend/src/App.tsx", "frontend/src/pages/HomePage.tsx",
                  "frontend/src/features/auth/AuthContext.tsx", "frontend/src/api/auth.ts"],
        "ask": """把应用外壳接起来：
- `AuthContext.tsx`：登录态（调当前登录态接口）、注册、登出；**不要用 localStorage**（会话在 httpOnly cookie 里）。
- `api/auth.ts`：封装调用后端接口（用 `frontend/src/api/index.ts` 的 axios 实例，baseURL 已是 `/api`）。
- `HomePage.tsx`：未登录时首页要有**一个可点击的注册入口**（测试会点击 `a[href="/register"]`，
  所以这里要渲染一个 `href="/register"` 的链接）；已登录时显示当前用户名，
  以及一个可访问名含 `退出登录`（或 `Sign out`）的**链接**用于登出。
- `App.tsx`：**输出完整文件**，用 `AuthProvider` 包住，并在 `<Routes>` 里加上 `/register` 路由。""",
    },
]


def _chunk_messages(chunk: dict, requirement_brief: str, design: str, skeleton: str,
                    written: list[str], side_extra: str) -> list[dict]:
    written_block = "\n".join(f"  - {w}" for w in written) if written else "  （还没有）"
    return [
        {"role": "system", "content": chunk["role"] + "只输出文件，不要解释。严格遵守给出的文件格式与边界。"},
        {"role": "user", "content": f"""{STACK_CONTRACT}

## 设计（已定稿，照它实现）

{design}

## 现有骨架

{skeleton}

{side_extra}

## 需求

{requirement_brief}

## 本次任务（只做这些文件，不要越界）

必须要输出的文件：
{chr(10).join("  - " + f for f in chunk["files"])}

已经写好的文件（可 import / 遵循它们的约定）：
{written_block}

{chunk["ask"]}

## 输出格式（严格遵守）

每个文件一段，路径从项目根算起：

===FILE: {chunk["files"][0]}===
```js
<完整文件内容>
```

不要输出任何其他文字。"""},
    ]


# ---------------------------------------------------------------- 解析与落盘

RE_FILE = re.compile(r"^===FILE:\s*(\S+?)\s*===\s*$", re.M)
RE_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)\n```", re.S)


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
        if content.strip():
            out[path] = content if content.endswith("\n") else content + "\n"
    return out


FORBIDDEN_PREFIXES = ("backend/src/database/",)


def write_files(output_dir: Path, files: dict[str, str], log=print) -> list[str]:
    """把生成的文件写进输出目录，带路径安全检查。返回实际写入的相对路径列表。"""
    written: list[str] = []
    for rel, content in files.items():
        rel = rel.strip().lstrip("./")
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise GenerationError(f"拒绝写入越界路径：{rel}")
        if not (rel.startswith("frontend/") or rel.startswith("backend/")):
            raise GenerationError(f"拒绝写入 frontend/ 与 backend/ 之外的路径：{rel}")
        if any(rel.startswith(p) for p in FORBIDDEN_PREFIXES):
            raise GenerationError(f"拒绝覆盖数据库脚手架：{rel}")
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
        text = chat(cfg, _chunk_messages(chunk, requirement_brief, design, skeleton, written, extra),
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
    return {"files": written, "files_by_chunk": written_by_chunk,
            "usage": usage, "calls": list(cfg.calls)}


def _side_extra(chunk: dict, output_dir: Path, written: list[str], *, log=print) -> str:
    """给这一块补一段「上游产物的原文」，让两边的接口契约逐字对齐。"""
    want: list[str] = []
    if chunk.get("side") == "frontend":
        want = [w for w in written if w.startswith("backend/src/routes/")]
    elif chunk.get("name") == "backend-routes":
        want = [w for w in written if w.startswith("backend/src/auth/")]
    blocks = []
    for rel in want:
        f = output_dir / rel
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            if len(text) > 6000:
                text = text[:6000] + "...(truncated)"
            blocks.append("### 上游已写好的文件 `" + rel + "`（照它的接口写，不要改它）\n```\n" + text + "\n```")
    if blocks:
        log(f"        （随本块带上 {len(want)} 个上游文件原文）")
    return "\n\n".join(blocks)
