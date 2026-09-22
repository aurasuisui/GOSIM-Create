"""通用分块器 —— **从 design JSON 派生生成块**，替掉写死的注册专用块（2026-09-21）。

## 为什么这是 M2 / M3b-1 的共同前置

`pipeline/generate/implement.py` 原先的 `CHUNKS` 是**模块级常量**：7 块里 6 块指着注册应用的
文件（`auth_repository.js` / `RegisterPage.tsx` / …），`ask` 也全是注册专用的校验清单。
**拿它跑 keep 只会产出注册应用 → 必然 0/32。**

设计阶段**本来就是通用的**（按需求产出 `routes` / `api_endpoints` / `db_tables` 的 JSON），
写死的只有"怎么把设计切成块"这一步。所以这里只做一件事：**读设计 → 派生块**。

## 派生规则（每条都对应一次实测教训）

| 块 | 来源 | 为什么这样切 |
|---|---|---|
| `backend-schema` | `db_tables` | 建表由管线落位（`generate/schema.py`），所以它是**独立产物**；表清单已经在 ask 里，**不带需求切片**（设计 JSON 本来每块都会带上） |
| `backend-<资源>` | `api_endpoints` 按 `/api/` 后第一段分组 | 一个资源一个文件 → **一文件一调用**（2 文件的块在比赛网关上连续 3/3 被断连） |
| `backend-app` | 全部资源 | `app.js` 要挂所有路由，必须最后写、且看到全部路由文件 |
| `frontend-page-<组件>` | `routes[]` | 一页一文件一调用 |
| `frontend-auth` | **条件块**：设计或需求里出现登录/会话/登出才生 | 不需要登录的应用不该被塞一个 AuthContext |
| `frontend-app` / `frontend-home` | 外壳 | 拆成两个一文件调用；`App.tsx` 要看到全部页面路径 |

## 每个块只带"自己那一片需求"

全量 brief 实测 15,923 字符（最大的那份到 11.6 万），逐块重发会立刻顶满预算。所以按**关键词命中**把需求叶子
分给块（路由路径 / 组件名 / 资源名出现在该叶子的场景文本里），每块只渲染自己那一片。
**没命中任何块的叶子不丢**：它们一起进 `frontend-app` 与 `frontend-home`（外壳最需要知道
"这个应用整体要干什么"）。
"""
from __future__ import annotations

import json
import re

# 出现这些词才生 `frontend-auth`（**条件块**：不需要登录的应用不该被塞 AuthContext）
AUTH_HINTS = ("登录", "logout", "sign in", "signin", "sign out", "signout", "session",
              "会话", "cookie", "auth", "认证", "账户")

RE_JSON_BLOCK = re.compile(r"\{.*\}", re.S)

# 每块需求切片的**硬上限**：超过就降级成"清单式摘要"。
# 为什么必须有（实测）：首页那条路由的组件名去掉后缀命中了同名的 21 条需求
# （这是个笔记应用），于是首页块被塞到 **22,404 字符 = 93% 预算**——而我们在 81% 上丢过一个 run
# （网关 RemoteDisconnected → RequestTooLarge → 整轮作废）。**任何一块都不许独自吃掉预算。**
SLICE_MAX_CHARS = 6000
RE_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)\n```", re.S)


def parse_design(text: str) -> dict:
    """从设计回复里取 JSON（容忍围栏与前后闲聊）。取不到返回 {}，由调用方兜底。"""
    if not text:
        return {}
    fence = RE_FENCE.search(text)
    body = fence.group(1) if fence else text
    m = RE_JSON_BLOCK.search(body)
    if not m:
        return {}
    try:
        got = json.loads(m.group(0))
    except Exception:  # noqa: BLE001 —— 设计不是严格 JSON 也不要炸
        return {}
    return got if isinstance(got, dict) else {}


def render_nodes(nodes, a11y_index) -> str:
    """把一组需求叶子渲染成 brief 片段（与 `build_requirement_brief` 同一格式）。"""
    by_req = a11y_index.by_req()
    parts: list[str] = []
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


def render_nodes_summary(nodes) -> str:
    """清单式摘要（id + 名字 + 一句描述）——切片超预算时的降级形态。"""
    lines = []
    for n in nodes:
        first = (n.description or "").strip().replace("\n", " ").split("。")[0][:80]
        lines.append(f"- {n.id}: {n.name}" + (f" —— {first}" if first else ""))
    return "\n".join(lines)


def _leaf_text(n) -> str:
    bits = [n.name or "", n.description or ""]
    for sc in n.scenarios:
        bits.append(sc.name or "")
        for st in sc.steps:
            bits.append(st.content or "")
    return "\n".join(bits)


def _endpoint_tokens(path: str) -> set[str]:
    """从端点路径取可命中的 token：`/api/notes/:id` → {notes}。

    ⚠️ 只留长度 ≥4 的（`id`/`me` 这类短词会命中任何需求文本 → 把需求错误地分给一堆块）。
    """
    return {t.lower() for t in re.findall(r"[A-Za-z][\w-]{3,}", path or "")
            if t.lower() not in {"api", "http", "https"}}


def resource_of(path: str) -> str:
    """/api/notes/123 → notes；/api/auth/register → auth；取不到就 misc。"""
    p = re.sub(r"^https?://[^/]+", "", (path or "").strip())
    m = re.match(r"^/api/([A-Za-z][\w-]*)", p)
    if m:
        return m.group(1).lower()
    m = re.match(r"^/([A-Za-z][\w-]*)", p)
    return m.group(1).lower() if m else "misc"


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_").lower()
    return s or "app"


def plan_chunks(design: dict, tree, a11y_index, *, req_ids: list[str] | None = None,
                log=print) -> tuple[list[dict], dict]:
    """返回 (chunks, report)。report 供验收用：覆盖度 / 分配情况 / 切片大小。"""
    routes = [r for r in (design.get("routes") or []) if isinstance(r, dict)]
    endpoints = [e for e in (design.get("api_endpoints") or []) if isinstance(e, dict)]
    tables = [t for t in (design.get("db_tables") or []) if isinstance(t, dict)]
    auth_note = str(design.get("auth") or design.get("session") or "")

    nodes = [n for n in tree.ordered() if n.is_leaf and n.scenarios]
    if req_ids:
        wanted = set(req_ids)
        nodes = [n for n in nodes if n.id in wanted]

    # ---- 资源组（从端点路径派生）----
    by_res: dict[str, list[dict]] = {}
    for e in endpoints:
        by_res.setdefault(resource_of(str(e.get("path") or "")), []).append(e)
    resources = sorted(by_res)

    # ---- 关键词表：资源 → token；路由 → token ----
    res_tokens = {r: (_endpoint_tokens(" ".join(str(e.get("path") or "") for e in eps)) | {r})
                  for r, eps in by_res.items()}
    route_tokens: list[tuple[str, str, set[str]]] = []       # (component, path, tokens)
    for r in routes:
        comp = str(r.get("component") or "").strip()
        path = str(r.get("path") or "")
        if not path:
            continue
        toks = {path.lower()}
        if comp:
            toks |= {comp.lower(), re.sub(r"(Page|View|Screen)$", "", comp).lower()}
        # ⚠️ 丢掉 `/` 与过短的 token：**`"/"` 会命中几乎所有需求文本**
        # （任何写路径的地方都有斜杠）→ 首页块被塞进 27 条需求、请求顶到 103% 预算（实测）。
        toks = {x for x in toks if len(x.strip("/")) >= 3}
        route_tokens.append((comp, path, toks))

    # ---- 需求 → 块 的分配 ----
    def hits(text: str) -> tuple[list[str], list[str]]:
        text = text.lower()
        res_hit = [r for r, toks in res_tokens.items() if any(t in text for t in toks)]
        page_hit = [comp for comp, _p, toks in route_tokens if any(t in text for t in toks)]
        return res_hit, page_hit

    node_res: dict[str, list] = {}
    node_page: dict[str, list] = {}
    misc: list = []
    for n in nodes:
        text = _leaf_text(n)
        rs, ps = hits(text)
        for r in rs:
            node_res.setdefault(r, []).append(n)
        # 组件名为空时用路径当键
        for comp, path, _t in route_tokens:
            if comp in ps or any(t in text.lower() for t in _t):
                node_page.setdefault(comp or path, []).append(n)
        if not rs and not ps:
            misc.append(n)

    # ---- 派生块 ----
    chunks: list[dict] = []

    chunks.append({
        "tier": "core",
        "name": "backend-schema", "side": "backend", "needs_skeleton": False,
        "role": "你是资深数据库工程师。", "files": ["backend/src/database/schema.sql"],
        "wants": (),                       # 它自己就是 schema，没有上游
        "nodes": [],                       # 设计 JSON 每块都会带上，schema 不需要需求切片
        "ask": "输出**建表脚本**（一个纯 SQL 文件；不要任何 JS、不要解释文字）：\n"
               "- 每条语句以 `;` 结尾，一律 `CREATE TABLE IF NOT EXISTS`（幂等）。\n"
               "- 覆盖设计里 `db_tables` 的**所有**表"
               + ("：" + "、".join(f"`{t.get('name')}`" for t in tables if t.get("name")) if tables else "")
               + "\n- 只输出 SQL：不要 `BEGIN`/`COMMIT`、不要 `PRAGMA`、不要 `INSERT`、不要 `ALTER`。\n"
               "- 列名与设计一致；唯一约束写 `UNIQUE(...)`；关联写 `REFERENCES 表(列)`；时间列给默认值。\n"
               "- 密码类字段只存摘要（列名用 `*_hash`）——**不要**出现明文密码列。\n"
               "- 这是整个应用**唯一**的建表位置（后端启动时执行）：应用用到的每个表都要在这里。",
    })

    for res in resources:
        eps = by_res[res]
        listing = "\n".join(
            f"  - `{e.get('method', 'GET')} {e.get('path')}` 请求：{e.get('request') or '—'}"
            f"  响应：{e.get('responses') or '—'}" for e in eps)
        chunks.append({
            "tier": "core",
            "name": f"backend-{_slug(res)}", "side": "backend", "needs_skeleton": False,
            "role": "你是资深 Node.js 后端工程师。",
            "files": [f"backend/src/routes/{_slug(res)}_routes.js"],
            "wants": ("schema",),          # 列名必须照 schema.sql
            "nodes": node_res.get(res, []),
            "ask": f"用**一个文件**实现 `{res}` 这个资源的 HTTP 层（Express Router，自包含："
                   "路由 + 校验 + 数据读写都在里面）：\n"
                   f"- 覆盖这些端点：\n{listing}\n"
                   "- 数据读写用脚手架：`const {{ run, get, all }} = require('../database');`"
                   "（**后端是 CommonJS：一律 require/module.exports，不要写 import/export**）。\n"
                   "- 表结构见随本块附上的 `schema.sql` 原文，**列名照它写**；"
                   "**不要在 JS 里写 CREATE TABLE**，也不要改 `backend/src/database/` 下的文件。\n"
                   "- 出错返回 JSON（含中文 message），状态码合理（400/404/409/500）。\n"
                   "- 导出 `module.exports = router`，由 `app.js` 挂载。",
        })

    if resources:
        chunks.append({
            "tier": "core",
            "name": "backend-app", "side": "backend", "needs_skeleton": True,
            "role": "你是资深 Node.js 后端工程师。",
            "files": ["backend/src/app.js"],
            "wants": ("schema", "backend/src/routes/"),
            # ⚠️ **不带需求切片**：它要的是"把哪些路由挂到哪个前缀"（在 ask 里，设计 JSON 每块都带）。
            # 带上全部资源的需求会把请求顶到 **95% 预算**（实测 22,895 字符）——
            # 这正是"每块只带自己那一片需求"要防的事。
            "nodes": [],
            "ask": "**输出完整文件**：把这些路由模块挂到对应的 `/api/<资源>`：\n"
                   + "\n".join(f"  - `backend/src/routes/{_slug(r)}_routes.js` → `/api/{r}`"
                               for r in resources)
                   + "\n- 原文件里 `// route modules imports` 与 `// register routes` 就是接入点。\n"
                     "- **保留原有的静态托管与 SPA fallback 逻辑**（`frontend/dist`）。\n"
                     "- 需要 cookie 解析就自己解析 `req.headers.cookie`，**不要引入新依赖**。\n"
                     "- **CommonJS**（require/module.exports）。",
        })

    for comp, path, _t in route_tokens:
        if not comp:
            continue
        # ⚠️ 首页由 `frontend-home` 拥有：否则两个块都会写 `pages/<Home>.tsx`，
        # 后写的覆盖先写的（一文件两主 —— 分块计划里必须是**每个文件恰好一个块**）。
        if path.rstrip("/") == "":
            continue
        purpose = next((str(r.get("purpose") or "") for r in routes
                        if str(r.get("component") or "").strip() == comp), "")
        chunks.append({
            "tier": "page",
            "name": f"frontend-page-{_slug(comp)}", "side": "frontend", "needs_skeleton": True,
            "role": "你是资深 React + TypeScript 前端工程师。",
            "files": [f"frontend/src/pages/{comp}.tsx"],
            "wants": ("backend/src/routes/", "authctx"),
            "route_path": path,        # 外壳块据此只接线"真的写出来了"的页面
            "nodes": node_page.get(comp, []),
            "ask": f"实现 `{comp}` 这一个页面组件（路由 `{path}`）：{purpose}\n"
                   "- 控件按需求里的**可访问名逐字**做：`<label htmlFor>` + `id` 原生关联。\n"
                   "- 实体值（用户名、标题…）**渲染在它自己的元素里**（`<span>{x}</span>`），"
                   "**不要拼进句子里**——精确文本查找会失配。\n"
                   "- 错误消息渲染在 `role=\"alert\"` 的元素里（非空、可见、中文）。\n"
                   "- 下拉用原生 `<select>`（初始占位项 `<option value=\"\">提示语</option>`）；"
                   "密码强度用 `<meter>` 且**必须带 `aria-label`**（只写 `<label htmlFor>` 时它的 "
                   "ARIA 名是空的，`getByRole('meter',{name})` 找不到）。\n"
                   "- 只用 react / react-router-dom / axios（走 `frontend/src/api/index.ts` 的实例，"
                   "baseURL 已是 `/api`）。\n"
                   "- 数据接口路径与字段**照随本块附上的后端路由原文**，不要自己另设。",
        })

    has_auth = any(h in (auth_note + " " + " ".join(_leaf_text(n) for n in nodes)).lower()
                   for h in AUTH_HINTS)
    if has_auth:
        chunks.append({
            "tier": "page",
            "name": "frontend-auth", "side": "frontend", "needs_skeleton": True,
            "role": "你是资深 React + TypeScript 前端工程师。",
            "files": ["frontend/src/features/auth/AuthContext.tsx"],
            "wants": ("backend/src/routes/",),
            "nodes": [n for v in node_res.values() for n in v],
            "ask": "实现登录态容器 `AuthContext.tsx`：挂载时向后端查当前会话；提供登录/登出"
                   "（有注册接口就一并提供）；**不要用 localStorage**（会话在 httpOnly cookie 里，"
                   "靠 cookie 重新拉取）。导出 `useAuth()` 供页面读写。"
                   "接口照随本块附上的后端路由原文。",
        })

    page_list = "\n".join(f"  - `{p}` → `frontend/src/pages/{c}.tsx`"
                          for c, p, _t in route_tokens if c)
    chunks.append({
        "name": "frontend-app", "side": "frontend", "needs_skeleton": True,
        "tier": "core",
        "role": "你是资深 React + TypeScript 前端工程师。",
        "files": ["frontend/src/App.tsx"],
        "wants": ("authctx",),
        "nodes": list(misc),
        "ask": "**输出完整文件**：把下面这些页面挂进 `<Routes>`"
               + ("（用 `AuthProvider` 包住）" if has_auth else "")
               + "：\n" + page_list
               + "\n- 保留原有的 `<BrowserRouter>` / `main.tsx` 接线，只改 `App.tsx`。",
    })
    home_comp = next((c for c, p, _t in route_tokens if p.rstrip("/") == ""), "")
    entry_links = "\n".join(f"  - `{p}`（`{'<Link to=\"' + p + '\">' if p else ''}`…）"
                            for c, p, _t in route_tokens if p.rstrip("/") and c)
    chunks.append({
        "name": "frontend-home", "side": "frontend", "needs_skeleton": True,
        "tier": "core",
        "role": "你是资深 React + TypeScript 前端工程师。",
        "files": [f"frontend/src/pages/{home_comp or 'HomePage'}.tsx"],
        "wants": ("authctx",),
        # ⚠️ 必须**并上**命中首页那条路由的需求叶子：首页路由（`/`）在页面循环里被跳过
        # （否则两个块会写同一个文件），所以它们的归属只能是这里——不减这一步就会**静默丢需求**
        # （实测：7 条需求没落进任何块）。
        "nodes": list(misc) + list(node_page.get(home_comp, [])),
        "ask": f"实现首页 `{home_comp or 'HomePage'}.tsx`（路由 `/`）："
               "未登录/无数据时给出**可点击的入口链接**——判据会按 `href` 点击进入，"
               "所以每个入口都必须是真链接（`<a href=\"…\">` / `<Link to=\"…\">`，"
               "**不要用 button 冒充**）：\n"
               + (entry_links or "  （没有其它路由时，给一句说明即可）")
               + "\n- 有数据时**把每条记录的关键字段渲染在它自己的元素里**（不要拼进句子），"
                 "并提供进入各页面的真链接（`role=link`）。",
    })

    for c in chunks:
        seen: set[str] = set()
        uniq = []
        for n in c["nodes"]:
            if n.id not in seen:
                seen.add(n.id)
                uniq.append(n)
        c["nodes"] = uniq
        c["brief_slice"] = render_nodes(uniq, a11y_index) if uniq else ""
        c["slice_degraded"] = False
        if len(c["brief_slice"]) > SLICE_MAX_CHARS:
            c["brief_slice"] = (render_nodes_summary(uniq)
                                + "\n\n（本块需求较多，上面是清单；**细节在你的页面/资源块里**，"
                                  "这里只需保证入口与整体形状对）")
            c["slice_degraded"] = True

    report = {
        "routes": [p for _c, p, _t in route_tokens],
        "components": [c for c, _p, _t in route_tokens if c],
        "tables": [str(t.get("name")) for t in tables if t.get("name")],
        "resources": resources,
        "has_auth": has_auth,
        "chunks": [{"name": c["name"], "files": c["files"],
                    "nodes": [n.id for n in c["nodes"]],
                    "slice_chars": len(c["brief_slice"]),
                    "slice_degraded": c.get("slice_degraded", False)} for c in chunks],
        "nodes_total": len(nodes),
        "nodes_misc": [n.id for n in misc],
        "nodes_per_res": {k: [n.id for n in v] for k, v in node_res.items()},
        "nodes_per_page": {k: [n.id for n in v] for k, v in node_page.items()},
    }
    return chunks, report
