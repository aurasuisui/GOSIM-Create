"""零 token 静态检查：**能在本地零成本判的，绝不留给判分**。

为什么必须先做这一层（旧管线的教训）：
  9/21 一天约 97 万 token、零人工满分 0 次，而三次跑的失败**全部是静态可判的**
  （缺 ARIA 名 / ESM-CJS 混用 / 少一个右括号）——"用生成去验静态规则"是最贵的浪费形态。

每条检查都必须"**能失败**"，否则不算判据（新检查先在历史产物上量精度再进闸门）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..reqcomp.loader import Tree, extract_targets

SCAN_GLOBS = ["frontend/src/**/*", "backend/src/**/*", "frontend/*.html", "frontend/*.json"]
SKIP_DIRS = {"node_modules", "dist", ".git", "build", "coverage"}
CODE_EXT = {".ts", ".tsx", ".js", ".jsx"}


# 🔴 **管线自己生成的文件**：它们是我们的代码，不该被"定向修复"重写。
# 实测事故：模板字符串判据把这些文件也报成 error → 修复轮把 `bootstrap.js` / `init_db.js`
# 换成了模型版本 → **后端直接起不来**（自伤）。所以所有判据都跳过这些路径。
PIPELINE_FILES = {
    "backend/src/index.js",
    "backend/src/auth.js",
    "backend/src/routes/_session.js",
    "backend/src/database/bootstrap.js",
    "backend/src/database/init_db.js",
    # ⚠️ `seed_db.js` 也算管线所有：模型写的那份会**自己往表里塞默认数据**，
    #    把我们从需求正文抽出来的评测预置数据覆盖掉（实测 `/api/workbooks` 返回空）。
    "backend/src/database/seed_db.js",
}


def is_pipeline_file(p: Path) -> bool:
    try:
        for root_name in ("backend", "frontend"):
            pass
    except Exception:  # noqa: BLE001
        return False
    parts = p.as_posix()
    return any(parts.endswith(f) for f in PIPELINE_FILES)


def _iter_files(root: Path):
    for pattern in SCAN_GLOBS:
        for p in root.glob(pattern):
            if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
                continue
            if is_pipeline_file(p):
                continue
            yield p


def check_structure(out: Path) -> list[dict]:
    """平台第一道闸：输出根必须有 frontend/ 与 backend/。"""
    findings: list[dict] = []
    for sub in ("frontend", "backend"):
        if not (out / sub).is_dir():
            findings.append({"code": "missing_dir", "severity": "error",
                             "message": f"缺少 {sub}/（平台第一道闸会直接判 run 失败）"})
    pkg = out / "backend" / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            if "start" not in (data.get("scripts") or {}):
                findings.append({"code": "no_start_script", "severity": "error",
                                 "message": "backend/package.json 没有 scripts.start（契约 C4）"})
        except Exception as exc:  # noqa: BLE001
            findings.append({"code": "bad_package_json", "severity": "error",
                             "message": f"backend/package.json 不是合法 JSON：{exc}"})
    return findings


QUOTES = "\"'`"


def check_delimiters(out: Path) -> list[dict]:
    """括号/引号平衡（词法近似：跳过字符串与注释，只数真实括号）。

    为什么值得写：旧管线实测"少一个右括号"直接让整份产物构建失败，
    而它是最典型的"静态可判、却被留给生成去修"的错误。
    """
    findings: list[dict] = []
    for p in _iter_files(out):
        if p.suffix not in CODE_EXT:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        depth = {"(": 0, "[": 0, "{": 0}
        pairs = {")": "(", "]": "[", "}": "{"}
        i, n = 0, len(text)
        quote = ""
        line_comment = block_comment = False
        bad = ""
        while i < n:
            ch = text[i]
            nxt = text[i + 1] if i + 1 < n else ""
            if line_comment:
                if ch == "\n":
                    line_comment = False
            elif block_comment:
                if ch == "*" and nxt == "/":
                    block_comment = False
                    i += 1
            elif quote:
                if ch == "\\":
                    i += 1
                elif ch == quote:
                    quote = ""
            elif ch == "/" and nxt == "/":
                line_comment = True
                i += 1
            elif ch == "/" and nxt == "*":
                block_comment = True
                i += 1
            elif ch in QUOTES:
                quote = ch
            elif ch in depth:
                depth[ch] += 1
            elif ch in pairs:
                depth[pairs[ch]] -= 1
                if depth[pairs[ch]] < 0:
                    bad = f"多余的 {ch}"
                    break
            i += 1
        if not bad:
            left = {k: v for k, v in depth.items() if v > 0}
            if left:
                bad = "未闭合：" + ", ".join(f"{k}x{v}" for k, v in left.items())
        if bad:
            findings.append({"code": "unbalanced", "severity": "error", "file": str(p),
                             "message": bad})
    return findings


def check_contract_strings(out: Path, tree: Tree) -> list[dict]:
    """需求里的靶子名是否**逐字**出现在产物里（大小写不敏感）。

    "本地多修一条，平台那次跑就少修一条"的便宜形态：判据按可访问名找，
    **中英不对应就是 0 分**（旧管线实测：把英文术语译成中文，整条丢掉）。
    ⚠️ 它**故意宽松**（子串、大小写不敏感）——要的是"有没有整片缺失"，
    精确的可命中性只能由真判分回答。
    """
    parts: list[str] = []
    for p in _iter_files(out):
        if p.suffix in CODE_EXT or p.suffix in {".css", ".html", ".json", ".sql"}:
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    blob = "\n".join(parts).lower()
    if not blob.strip():
        return [{"code": "no_source", "severity": "error", "message": "产物里没有任何源码文件"}]
    findings: list[dict] = []
    for node in tree.scorable_leaves():
        for t in extract_targets(node):
            if t.is_regex or len(t.name) < 3:
                continue
            if t.name.lower() not in blob:
                findings.append({"code": "target_missing", "severity": "warning",
                                 "req_id": node.id, "target": t.name, "role": t.role,
                                 "message": f"{node.id}: 靶子 {t.name!r} 在产物里找不到"})
    return findings


# 模板真正装了的包（dependencies；devDependencies 在平台/本地都可能不装）
BACKEND_ALLOWED = {"express", "body-parser", "cors", "sqlite3"}
FRONTEND_ALLOWED = {"react", "react-dom", "react-router-dom", "axios"}
NODE_BUILTINS = {"fs", "path", "crypto", "util", "os", "url", "http", "https", "events",
                 "stream", "zlib", "querystring", "assert", "buffer", "child_process",
                 "string_decoder", "timers", "tty", "v8", "vm", "worker_threads"}
RE_REQUIRE = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")
RE_IMPORT = re.compile(r"^\s*import\s+(?:[^\n]*?from\s+)?['\"]([^'\"]+)['\"]", re.M)


def _is_local(mod: str) -> bool:
    return mod.startswith(".") or mod.startswith("/") or mod.startswith("node:")


def check_imports(out: Path) -> list[dict]:
    """产物 import/require 的包是否真的装了。

    为什么必须静态拦：实测模型写了 `require('bcryptjs')`，而模板只装了 4 个包 →
    **后端起不来**（Cannot find module）→ 判据全挂。而它在本地是"能构建"的，
    只有真起服务才暴露 —— 属于"能零成本静态判、别留给判分"的那一类。
    """
    findings: list[dict] = []
    for p in _iter_files(out):
        if p.suffix not in CODE_EXT and p.suffix != ".js":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        frontend = "frontend" in p.parts
        allowed = FRONTEND_ALLOWED if frontend else BACKEND_ALLOWED
        mods = set(RE_REQUIRE.findall(text)) | set(RE_IMPORT.findall(text))
        for mod in sorted(mods):
            if _is_local(mod):
                continue
            base = mod.split("/")[0] if not mod.startswith("@") else "/".join(mod.split("/")[:2])
            if base.startswith("node:"):
                continue
            if base in allowed or base in NODE_BUILTINS:
                continue
            findings.append({"code": "missing_dependency", "severity": "error", "file": str(p),
                             "message": f"引用了没装的包 {mod!r}（{p.name}）→ 启动/构建会失败"})
    return findings


RE_API_CALL = re.compile(r"['\"`](/api/[A-Za-z0-9_\-/]*)")


def _mounts_and_routes(out: Path) -> set[str]:
    """后端**实际可达**的路径集合 = 每个 router 的挂载前缀 + 它自己声明的子路径。

    为什么不能只看 `app.use('/api/x')` 的前缀：管线自己的会话路由是挂在 `/api` 上的，
    它提供 `/me`、`/logout`、`/check` 等子路径 —— 只看前缀会报 16 条**假阳性**
    （"前端调了 /api/me 而后端没挂"），而假阳性比漏报更贵。
    """
    paths: set[str] = set()
    app_js = out / "backend" / "src" / "app.js"
    routes_dir = out / "backend" / "src" / "routes"
    mounts: dict[str, str] = {}
    if app_js.is_file():
        text = app_js.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"app\.(?:use|get|post|put|patch|delete)\(\s*['\"](/api[^'\"]*)['\"]\s*,?\s*(\w+)?", text):
            prefix, ident = m.group(1).rstrip("/"), m.group(2) or ""
            if ident:
                mounts[ident] = prefix
            else:
                paths.add(prefix)
    if routes_dir.is_dir():
        for f in sorted(routes_dir.glob("*.js")):
            ident = re.sub(r"[^A-Za-z0-9_]", "_", f.stem) + "Router"
            prefix = mounts.get(ident, "/api/" + f.stem)
            body = f.read_text(encoding="utf-8", errors="replace")
            for sub in re.findall(r"router\.(?:get|post|put|patch|delete|all)\s*\(\s*['\"`]([^'\"`]*)", body):
                sub = sub.rstrip("/")
                full = (prefix + sub) if sub.startswith("/") else (prefix + "/" + sub)
                paths.add(full.rstrip("/") or "/")
    return paths


def _norm_api(path: str) -> str:
    """把路径里的**任何占位段**归一成 `:id`：`:bookId` / `${id}` / `{id}` / 数字 都算。

    不归一就会出现"前端写 `/api/books/${id}` 而后端声明 `/api/books/:bookId`"被判不匹配
    —— 又一条假阳性。
    """
    out = []
    for s in path.split("/"):
        if not s:
            continue
        if s.isdigit() or s.startswith(":") or s.startswith("{") or "$" in s:
            out.append(":id")
        else:
            out.append(s)
    return "/" + "/".join(out)


def check_api_paths(out: Path) -> list[dict]:
    """前端调用的 `/api/...` 是否真的被后端挂载。

    为什么必须静态拦：实测模型写了 `routes/login.js` 与页面里的 `fetch('/api/login')`，
    却**没在 app.js 里挂**那个 router → `POST /api/login` 404 → 登录永远失败，
    而所有下游判据（都要先登录）跟着全挂。现在挂载表由管线生成，这条检查保证两边一致。
    """
    reachable = {_norm_api(p) for p in _mounts_and_routes(out)}
    if not reachable:
        return []
    findings: list[dict] = []
    for p in _iter_files(out):
        if "frontend" not in p.parts or p.suffix not in CODE_EXT:
            continue
        for m in RE_API_CALL.finditer(p.read_text(encoding="utf-8", errors="replace")):
            call = _norm_api(m.group(1))
            # 模板串里的 `${id}` 会被正则截断成 `/api/chapters/`，所以要允许"前缀匹配"：
            # 只要**存在一条可达路由以它为前缀**就算对上（否则又是一堆假阳性）。
            if call in reachable or any(r.startswith(call + "/") for r in reachable):
                continue
            findings.append({"code": "api_not_mounted", "severity": "error", "file": str(p),
                             "message": f"前端调用 {m.group(1)!r}，后端没有任何路由匹配它"})
    return findings


def _norm_api(path: str) -> str:
    """把路径里的**任何占位段**归一成 `:id`：`:bookId` / `${id}` / `{id}` / 数字 都算。

    不归一就会出现"前端写 `/api/books/${id}` 而后端声明 `/api/books/:bookId`"被判不匹配
    —— 又一条假阳性。
    """
    out = []
    for s in path.split("/"):
        if not s:
            continue
        if s.isdigit() or s.startswith(":") or s.startswith("{") or "$" in s:
            out.append(":id")
        else:
            out.append(s)
    return "/" + "/".join(out)


def _mounts_and_routes(out: Path) -> set[str]:
    """后端**实际可达**的路径 = 每个 router 的挂载前缀 + 它自己声明的子路径。

    为什么不能只看 `app.use('/api/x')` 的前缀：管线自己的会话路由挂在 `/api` 上，
    它提供 `/me`、`/logout`、`/check` —— 只看前缀会报 16 条**假阳性**
    （"前端调了 /api/me 而后端没挂"）。假阳性比漏报更贵：它会让闭环去修不存在的问题。
    """
    paths: set[str] = set()
    app_js = out / "backend" / "src" / "app.js"
    routes_dir = out / "backend" / "src" / "routes"
    # ⚠️ 同一个 router **可以挂到多个前缀**（管线的会话路由就挂在 `/api` 与 `/api/auth` 两处）。
    # 只留最后一个前缀会漏掉另一半路径（实测因此把 `/api/me` 报成"没有路由匹配"）。
    mounts: dict[str, list[str]] = {}
    if app_js.is_file():
        text = app_js.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
                r"app\.(?:use|get|post|put|patch|delete)\(\s*['\"](/api[^'\"]*)['\"]\s*,?\s*(\w+)?", text):
            prefix, ident = m.group(1).rstrip("/"), m.group(2) or ""
            if ident:
                mounts.setdefault(ident, []).append(prefix)
            else:
                paths.add(prefix)
    if routes_dir.is_dir():
        for f in sorted(routes_dir.glob("*.js")):
            ident = re.sub(r"[^A-Za-z0-9_]", "_", f.stem) + "Router"
            prefixes = mounts.get(ident) or ["/api/" + f.stem]
            body = f.read_text(encoding="utf-8", errors="replace")
            subs = re.findall(r"router\.(?:get|post|put|patch|delete|all)\s*\(\s*['\"`]([^'\"`]*)", body)
            for prefix in prefixes:
                for sub in subs:
                    sub = sub.rstrip("/")
                    full = (prefix + sub) if sub.startswith("/") else (prefix + "/" + sub)
                    paths.add(full.rstrip("/") or "/")
    return paths


PIPELINE_MODULES = {"auth": "backend/src/auth.js"}


def check_pipeline_module_members(out: Path) -> list[dict]:
    """引用了管线提供模块里**不存在**的成员（例如 `auth.requireAuth`）。

    实测形态：模型写 `router.post('/', auth.requireAuth, handler)`，而 auth.js 当时没有 requireAuth
    → 中间件是 undefined → `TypeError: argument handler must be a function` → **后端起不来**，
    而语法检查全绿。这条检查把"成员是否存在"变成可静态判的。
    """
    findings: list[dict] = []
    for mod, rel in PIPELINE_MODULES.items():
        src = out / rel
        if not src.is_file():
            continue
        text = src.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"module\.exports\s*=\s*\{(.*?)\}\s*;", text, re.S)
        exported = set()
        if m:
            for part in m.group(1).split(","):
                name = part.split(":")[-1].strip()
                if name:
                    exported.add(name)
        if not exported:
            continue
        for p in _iter_files(out):
            if "frontend" in p.parts or p.suffix != ".js":
                continue
            body = p.read_text(encoding="utf-8", errors="replace")
            for mm in re.finditer(rf"\b{mod}\.([A-Za-z_$][\w$]*)", body):
                if mm.group(1) not in exported:
                    findings.append({"code": "missing_module_member", "severity": "error", "file": str(p),
                                     "message": (f"{mod}.{mm.group(1)} 不存在（{rel} 只导出 "

                                                 + ", ".join(sorted(exported)) + "）")})
    return findings


RE_ROUTE_HEAD = re.compile(r"router\.(get|post|put|patch|delete|all|use)\s*\(")


def _route_calls(text: str):
    r"""产出 (verb, [顶层参数, ...])。

    参数切分**必须按括号深度**：实测用 `,(?![^()]*\))` 这种正则会把
    `async (req, res) => { const { name, description } = req.query; … }` 里的解构键
    当成"处理函数名"→ 报四条假阳性（"假阳性比漏报更贵：它会让闭环去修不存在的问题"）。
    """
    for m in RE_ROUTE_HEAD.finditer(text):
        i = m.end()
        depth = 1
        quote = ""
        start = i
        while i < len(text) and depth > 0:
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
            elif ch in "\"'`":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = text[start:i]
        parts: list[str] = []
        depth2 = 0
        quote2 = ""
        cur = ""
        for ch in body:
            if quote2:
                cur += ch
                if ch == quote2:
                    quote2 = ""
                continue
            if ch in "\"'`":
                quote2 = ch
                cur += ch
            elif ch in "([{":
                depth2 += 1
                cur += ch
            elif ch in ")]}":
                depth2 -= 1
                cur += ch
            elif ch == "," and depth2 == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        yield m.group(1), parts


RE_ROUTE_CALL = re.compile(r"router\.(get|post|put|patch|delete)\(([^;]{1,400}?)\)\s*;", re.S)
RE_DEF = re.compile(r"(?:function\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=)")


def check_route_handlers(out: Path) -> list[dict]:
    """路由注册的处理函数**必须存在**。

    为什么必须静态拦：实测模型写了 `router.post('/x', someHandler)` 而 `someHandler` 根本没定义 →
    `require()` 那一刻就 `TypeError: argument handler must be a function` → **整个后端进程起不来**，
    而语法检查（括号/引号）**全绿** —— 它只在运行期炸。
    """
    findings: list[dict] = []
    for p in sorted((out / "backend" / "src" / "routes").glob("*.js")) if (out / "backend" / "src" / "routes").is_dir() else []:
        text = p.read_text(encoding="utf-8", errors="replace")
        defined = set()
        for m in RE_DEF.finditer(text):
            defined.add(m.group(1) or m.group(2))
        for m in re.finditer(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require", text):
            for part in m.group(1).split(","):
                name = part.split(":")[-1].strip()
                if name:
                    defined.add(name)
        for verb, args in _route_calls(text):
            for arg in args[1:]:
                arg = arg.strip()
                if not arg or arg[0] in "{(`\"'" + chr(39) + "":
                    continue
                if arg.startswith(("function", "async", "await")):
                    continue
                ident = arg.split(".")[0].strip().rstrip(")")
                if re.fullmatch(r"[A-Za-z_$][\w$]*", ident) and ident not in defined:
                    findings.append({"code": "undefined_handler", "severity": "error", "file": str(p),
                                     "message": (f"{verb.upper()} 注册的处理函数 {ident!r} 未定义"
                                                 " → 后端 require 时就崩，整个应用起不来")})
    return findings


RE_AUTH_GATE = re.compile(
    r"if\s*\(\s*!\s*(user|isAuthenticated|loggedIn|auth|currentUser|session)\b[^)]*\)\s*\{?\s*"
    r"(return|setError)", re.I)


def check_page_auth_gates(out: Path) -> list[dict]:
    """页面里有没有"登录门"（未登录就直接 return 一个登录提示）。

    为什么必须静态拦：实测 `BooksPage` 开头就是
    `if (!isAuthenticated) return <div><button>Login</button></div>` ——
    而判据常常是 **不登录** 直接打开列表页、然后点某条记录的名字：
    页面渲染成登录提示 → 找不到那个名字 → 超时（本轮 31 条失败里绝大多数是这一个形态）。
    登录态只该影响额外的提示条，**不能拦住主体内容**。
    """
    findings: list[dict] = []
    pages = out / "frontend" / "src" / "pages"
    if not pages.is_dir():
        return findings
    for p in sorted(pages.glob("*.tsx")):
        text = p.read_text(encoding="utf-8", errors="replace")
        m = RE_AUTH_GATE.search(text)
        if m:
            findings.append({"code": "page_auth_gate", "severity": "error", "file": str(p),
                             "message": (f"{p.name} 里有登录门（{m.group(0).strip()[:40]}…）→ "
                                         "未登录时整页空白，判据点不到任何名字")})
    return findings


RE_REL_IMPORT = re.compile(r"""(?:from|import)\s*[\"'](\.{1,2}/[^\"']+)[\"']""")


def check_missing_relative_imports(out: Path) -> list[dict]:
    """相对导入指向**不存在的文件** → vite/rollup 直接 `Could not resolve` → 整个前端构建失败。

    为什么要静态拦（实测 2026-09-28）：模型写的 `PageEditPage.tsx` 里有一句
    `import { api } from '../services/api'`，而产物里**没有 `frontend/src/services/api`** →
    平台/本地构建整题失败（这一条能直接让一整轮 0 分）。
    判据只覆盖"装没装包"，覆盖不到"相对路径指到不存在的地方"。
    """
    root = Path(out) / "frontend" / "src"
    if not root.is_dir():
        return []
    exts = ("", ".ts", ".tsx", ".js", ".jsx", ".json", "/index.ts", "/index.tsx", "/index.js")
    findings: list[dict] = []
    for p in list(root.rglob("*.tsx")) + list(root.rglob("*.ts")) + list(root.rglob("*.js")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in RE_REL_IMPORT.finditer(text):
            spec = m.group(1)
            base = (p.parent / spec).resolve()
            if any(Path(str(base) + e).exists() for e in exts):
                continue
            findings.append({"code": "missing_relative_import", "severity": "error", "file": str(p),
                             "message": (f"相对导入 `{spec}` 指向不存在的文件 → 构建会报 "
                                         "`Could not resolve`；请删掉这个 import 或改成已有的模块（例如直接用 axios 调 /api）")})
            break
    return findings


def check_template_literals(out: Path) -> list[dict]:
    """产物里是否用了模板字符串（反引号）。

    为什么当成 error：实测反引号在长文件里会被**丢掉一半**（丢开引号 → esbuild `Expected ";" but found "{"`），
    后果是**整个前端构建失败** → 那一轮判据全废；后端同理会让服务起不来。
    与其每次靠修复轮去救，不如**不让它出现在产物里**。
    """
    findings: list[dict] = []
    for p in _iter_files(out):
        if p.suffix not in CODE_EXT:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "`" in text:
            line_no = next((i for i, l in enumerate(text.splitlines(), 1) if "`" in l), 0)
            findings.append({"code": "template_literal", "severity": "error", "file": str(p),
                             "message": (f"第 {line_no} 行用了模板字符串（反引号）→ 容易被截断成语法错，"
                                         "请改用 + 拼接或 String()")})
    return findings


RE_JSX_ARROW = re.compile(r"^[^/\n]*->")


def check_jsx_text_arrows(out: Path) -> list[dict]:
    """`.tsx` 的 **JSX 文本里**出现 `->`：esbuild 会直接报 `The character ">" ...` → 构建失败。

    为什么必须静态拦（实测，2026-09-28 平台】：github 题的 `PullRequestsPage.tsx:109` 写了
    `<span>{pr.branch} -> {pr.targetBranch}</span>` —— 本地 esbuild 复现同样报错，
    而**平台那次跑没有 esbuild**（agent 里没装依赖）→ 只能靠静态判据提前发现。
    修法：改成 `→`，或把箭头放进 `{}` 里（`{ ' -> ' }`）。
    """
    findings: list[dict] = []
    for p in _iter_files(out):
        if p.suffix != ".tsx":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "->" not in line or "=>" in line or line.lstrip().startswith(("//", "*", "/*")):
                continue
            findings.append({"code": "jsx_text_arrow", "severity": "error", "file": str(p),
                             "message": (f"第 {i} 行 JSX 文本里有 `->` → esbuild 会报 "
                                         "`The character \">\" ...` 导致构建失败；请改用 `→` 或放进 {} 里")})
            break
    return findings


def check_placeholders(out: Path) -> list[dict]:
    findings: list[dict] = []
    schema = out / "backend" / "src" / "database" / "schema.sql"
    if schema.is_file() and not re.search(r"CREATE\s+TABLE",
                                          schema.read_text(encoding="utf-8", errors="replace"), re.I):
        findings.append({"code": "no_tables", "severity": "warning", "file": str(schema),
                         "message": "schema.sql 里没有 CREATE TABLE"})
    app_tsx = out / "frontend" / "src" / "App.tsx"
    if app_tsx.is_file() and app_tsx.stat().st_size < 300:
        findings.append({"code": "shell_placeholder", "severity": "warning", "file": str(app_tsx),
                         "message": "App.tsx 太小，可能还是模板占位"})
    return findings


def check(out: Path | str, tree: Tree) -> dict:
    out = Path(out)
    findings = (check_structure(out) + check_delimiters(out) + check_imports(out)
                + check_api_paths(out) + check_route_handlers(out) + check_page_auth_gates(out)
                + check_template_literals(out) + check_jsx_text_arrows(out)
                + check_missing_relative_imports(out)
                + check_pipeline_module_members(out)
                + check_contract_strings(out, tree) + check_placeholders(out))
    by_code: dict[str, int] = {}
    for f in findings:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    errors = [f for f in findings if f.get("severity") == "error"]
    missing = [f for f in findings if f["code"] == "target_missing"]
    return {
        "summary": {
            "errors": len(errors),
            "warnings": len(findings) - len(errors),
            "by_code": by_code,
            "targets_missing": len(missing),
            "files_scanned": sum(1 for _ in _iter_files(out)),
        },
        "findings": findings[:400],
    }