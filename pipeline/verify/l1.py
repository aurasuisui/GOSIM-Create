"""L1 闸门（可执行性）—— 机器自动发现 M3a 那类缺陷。

**为什么是静态的**：`PLAN.md` §4.2 的 L1 原文包含"应用能 build + start"，
但**平台里 agent 跑起来之后没有网络**（`docs/02` §一 实测），`npm install` 做不了。
所以这里只做**不需要依赖树的静态检查**——真正 build/start 由平台在做（V5 日志已证实），
我们的职责是**在把产物交出去之前，用静态手段把明显会炸的东西挑出来**。

覆盖 M3a 实测的三类缺陷：

| M3a 缺陷 | 本模块的检查 |
|---|---|
| 5.1 相对 import 路径写错（`../api/auth` 少一级）→ 构建失败 | `check_imports` |
| 5.3 场景入口不可达（`a[href="/register"]` 没有）／路由没注册 | `check_routes_and_links` |
| 5.3 需求声明的可访问名在源码里根本不存在 | `check_accessible_names` |
| 另外：引用了未声明的依赖、动过数据库脚手架 | `check_deps` / `check_scaffold_intact` |

**它不做什么**：不判"语义对不对"（例如"首页要用 `getByText(username, {exact:true})` 能匹配的元素显示用户名"）。
那类要靠 `selfcheck.py` 的模型自检 + 定向修复。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FRONTEND_EXT = (".ts", ".tsx", ".js", ".jsx")
NODE_BUILTINS = {
    "fs", "path", "crypto", "http", "https", "os", "util", "url", "events", "stream",
    "child_process", "assert", "buffer", "zlib", "net", "tty", "querystring", "timers",
    "sqlite3", "express", "cors", "body-parser", "axios", "react", "react-dom", "react-router-dom",
}

RE_TS_IMPORT = re.compile(r"""(?:from|import)\s*['"]([^'"]+)['"]""")
RE_REQUIRE = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")
RE_ROUTE = re.compile(r"""<Route\s+[^>]*path\s*=\s*['"]([^'"]+)['"]""", re.S)
RE_ROUTE_OBJ = re.compile(r"""path\s*:\s*['"](/[^'"]*)['"]""")
RE_HREF = re.compile(r"""href\s*=\s*['"]([^'"]+)['"]""")
# React Router 的 <Link to="..."> / <NavLink to="..."> 运行时会渲染成 href，静态只看 href= 会漏
RE_TO = re.compile(r"""<\s*(?:Link|NavLink)\b[^>]*?\bto\s*=\s*['"]([^'"]+)['"]""", re.S)
# markdown 里的图片/链接路径（如 ./reference/register.png）不是页面路径
RE_MD_RESOURCE = re.compile(r"\]\(([^)]*)\)")
RE_PATH_IN_TEXT = re.compile(r"(?<![\w.\-])((?:/[a-zA-Z][\w\-/]*))")


def _iter_sources(output_dir: Path):
    for top in ("frontend/src", "backend/src"):
        base = output_dir / top
        if base.is_dir():
            for f in sorted(base.rglob("*")):
                if f.is_file() and f.suffix in FRONTEND_EXT:
                    yield f


def _resolve_relative(importer: Path, spec: str) -> bool:
    target = (importer.parent / spec).resolve()
    if target.is_file():
        return True
    for ext in FRONTEND_EXT + (".json",):
        if target.with_suffix(ext).is_file():
            return True
        cand = target / f"index{ext}"
        if cand.is_file():
            return True
    return False


def check_imports(output_dir: Path) -> list[dict]:
    """每个相对 import 都要能落到一个真实文件上；裸包名必须在 package.json 里声明。"""
    findings: list[dict] = []
    deps: set[str] = set()
    for pkg in ("frontend/package.json", "backend/package.json"):
        f = output_dir / pkg
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                deps |= set((data.get("dependencies") or {}).keys())
                deps |= set((data.get("devDependencies") or {}).keys())
            except Exception:  # noqa: BLE001
                findings.append({"kind": "package-json-unparsable", "file": pkg,
                                 "detail": "package.json 不是合法 JSON —— 依赖装不上，构建必挂",
                                 "hint": "修复 JSON 语法"})

    for src in _iter_sources(output_dir):
        rel = src.relative_to(output_dir).as_posix()
        text = src.read_text(encoding="utf-8", errors="replace")
        specs = set(RE_TS_IMPORT.findall(text)) | set(RE_REQUIRE.findall(text))
        for spec in specs:
            if spec.startswith("."):
                if not _resolve_relative(src, spec):
                    findings.append({
                        "kind": "unresolved-import", "file": rel,
                        "detail": f"相对 import 解析不到文件：'{spec}'（从 {rel} 出发）",
                        "hint": "改对相对层级（数一下 src 下自己到目标的目录深度）",
                    })
            elif spec.startswith("node:"):
                continue
            else:
                pkg = spec if not spec.startswith("@") else "/".join(spec.split("/")[:2])
                pkg = pkg.split("/")[0] if not pkg.startswith("@") else pkg
                if pkg not in deps and pkg not in NODE_BUILTINS:
                    findings.append({
                        "kind": "undeclared-dependency", "file": rel,
                        "detail": f"引用了未声明的包 '{pkg}'（package.json 里没有，平台装不上）",
                        "hint": "改用已声明的依赖，或去掉这个 import",
                    })
    return findings


def check_routes_and_links(output_dir: Path, requirement_brief: str) -> list[dict]:
    """需求里点名的路径，必须既有路由注册、又有可达入口（M3a 的 `a[href="/register"]` 属此）。"""
    findings: list[dict] = []
    # 先剥掉 markdown 资源语法里的路径（`](./reference/register.png)`）：那些是图片，不是页面
    text_for_paths = RE_MD_RESOURCE.sub(" ", requirement_brief)
    wanted: set[str] = set()
    for m in RE_PATH_IN_TEXT.finditer(text_for_paths):
        path = m.group(1)
        # 只收"看起来像页面路径"的（排除 /api/… 与文件扩展名）
        if path.startswith("/api"):
            continue
        if re.search(r"\.\w{2,4}$", path):
            continue
        if len(path) > 1 and path.count("/") <= 2:
            wanted.add(path)

    app_files = list((output_dir / "frontend/src").rglob("*.tsx")) if (output_dir / "frontend/src").is_dir() else []
    front_blob = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in app_files)
    routes = set(RE_ROUTE.findall(front_blob)) | set(RE_ROUTE_OBJ.findall(front_blob))
    hrefs = set(RE_HREF.findall(front_blob)) | set(RE_TO.findall(front_blob))

    for path in sorted(wanted):
        if path == "/":
            continue
        if path not in routes:
            findings.append({
                "kind": "route-missing", "file": "frontend/src/App.tsx",
                "detail": f"需求点名了路径 {path}，但前端没有为它注册路由（测试可能直接访问或点击进入）",
                "hint": f"在 <Routes> 里加 <Route path=\"{path}\" …>",
            })
        if path not in hrefs:
            findings.append({
                "kind": "entry-link-missing", "file": "frontend/src",
                "detail": f"需求点名了 {path}，但没有任何元素用 href=\"{path}\" 指向它"
                          "（测试可能用 `a[href=\"{path}\"]` 点击进入）",
                "hint": f"在首页或导航里加一个 <a href=\"{path}\"> / <Link to=\"{path}\">",
            })
    return findings


def check_accessible_names(output_dir: Path, required_names: list[str]) -> list[dict]:
    """需求声明的可访问名，至少要能在前端源码里出现（否则实现根本没打算兑现它）。"""
    findings: list[dict] = []
    front = output_dir / "frontend/src"
    blob = ""
    if front.is_dir():
        blob = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in front.rglob("*")
                         if f.is_file() and f.suffix in FRONTEND_EXT)
    for name in required_names:
        if name and name not in blob:
            findings.append({
                "kind": "accessible-name-missing", "file": "frontend/src",
                "detail": f"需求声明的可访问名 {name!r} 在前端源码里一次都没出现",
                "hint": "用 <label htmlFor> + id 原生关联，或把控件包在 <label> 里",
            })
    return findings


def check_scaffold_intact(output_dir: Path, template_dir: Path) -> list[dict]:
    """数据库脚手架必须没被改过（改了就偏离平台契约）。"""
    findings: list[dict] = []
    src = template_dir / "backend/src/database"
    dst = output_dir / "backend/src/database"
    if not src.is_dir() or not dst.is_dir():
        return findings
    for f in src.glob("*.js"):
        other = dst / f.name
        if not other.is_file():
            findings.append({"kind": "scaffold-missing", "file": f"backend/src/database/{f.name}",
                             "detail": "数据库脚手架文件不见了", "hint": "从模板恢复"})
        elif f.name not in ("init_db.js",):        # init_db.js 会被 apply_known_fixes 有意修补
            if f.read_bytes() != other.read_bytes():
                findings.append({"kind": "scaffold-modified",
                                 "file": f"backend/src/database/{f.name}",
                                 "detail": "数据库脚手架被改动了（契约要求复用而不是重建）",
                                 "hint": "恢复模板原文，改用自己的仓储层"})
    return findings


def check_scripts(output_dir: Path) -> list[dict]:
    """平台要求的 npm scripts 必须在（C4/C5）。"""
    findings: list[dict] = []
    for pkg, needed in (("backend/package.json", ("start", "db:prepare:e2e")),
                        ("frontend/package.json", ("build",))):
        f = output_dir / pkg
        if not f.is_file():
            findings.append({"kind": "package-missing", "file": pkg, "detail": "文件不存在",
                             "hint": "从模板恢复"})
            continue
        try:
            scripts = json.loads(f.read_text(encoding="utf-8")).get("scripts") or {}
        except Exception:  # noqa: BLE001
            continue
        for s in needed:
            if s not in scripts:
                findings.append({"kind": "script-missing", "file": pkg,
                                 "detail": f"缺少脚本 {s!r}", "hint": f"补回 \"{s}\""})
    return findings


def run_l1(output_dir: Path, *, requirement_brief: str, required_names: list[str],
           template_dir: Path, log=print) -> dict:
    """跑完整 L1，返回 {passed, findings}。findings 可**直接**喂给定向修复。"""
    findings: list[dict] = []
    checks = [
        ("imports", lambda: check_imports(output_dir)),
        ("routes_links", lambda: check_routes_and_links(output_dir, requirement_brief)),
        ("accessible_names", lambda: check_accessible_names(output_dir, required_names)),
        ("scaffold", lambda: check_scaffold_intact(output_dir, template_dir)),
        ("scripts", lambda: check_scripts(output_dir)),
    ]
    per_check: dict[str, int] = {}
    for name, fn in checks:
        try:
            got = fn()
        except Exception as exc:  # noqa: BLE001 —— 检查器自己崩了也要报出来
            got = [{"kind": "checker-crashed", "file": name, "detail": repr(exc), "hint": "修检查器"}]
        per_check[name] = len(got)
        findings.extend(got)

    log("  L1 闸门：")
    for name, n in per_check.items():
        log(f"    {'✅' if n == 0 else '❌'} {name:18s} {n} 项")
    for f in findings[:12]:
        log(f"      · [{f['kind']}] {f['file']}: {f['detail']}")
    if len(findings) > 12:
        log(f"      … 其余 {len(findings) - 12} 项")
    return {"passed": not findings, "findings": findings, "per_check": per_check}
