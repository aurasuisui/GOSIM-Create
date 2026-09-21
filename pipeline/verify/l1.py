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

from generate.schema import (
    BEGIN_MARK, INIT_DB_REL, SCHEMA_REL, read_schema_statements,
)
from verify.jsscan import scan

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


RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
RE_LINE_COMMENT = re.compile(r"//[^\n]*")
# ESM 语法探测（后端必须是 CommonJS，见 check_module_system）
RE_ESM_IMPORT = re.compile(r"^[ 	]*import\s+(?:[\w{*]|\()", re.M)
RE_ESM_EXPORT = re.compile(r"^[ 	]*export\s+(?:default|const|let|var|function|class|\{|async)", re.M)
# `<meter` 到它的第一个 `>`（属性区可能跨行）——用于查"有没有 aria-label"
RE_METER = re.compile(r"<meter\b[^>]*>", re.I | re.S)
RE_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)", re.I)


def strip_js_comments(text: str) -> str:
    """先剥掉 JS 注释再匹配 SQL。

    为什么必须有这一步：`seed_db.js` 的 JSDoc 里写着 "…FROM tests…" 之类，
    不解注释就会把**注释当 SQL 读** → 报出 `test_harness` / `tests` 两个不存在的表
    （计划已定位：`seed_db.js:15-16`）。
    """
    return RE_LINE_COMMENT.sub("", RE_BLOCK_COMMENT.sub("", text))


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


def check_prose_names(output_dir: Path, soft_names) -> list[dict]:
    """**散文靶子**（§4.1 第 5 条抽出来的那些）单独查，且**不判死**。

    为什么必须分开（裁定的约束 3）：散文抽取会把 `0 results`、`count` 这类词一并捞进来，
    它们是**文本断言/测试数据**，不是应用该暴露的可访问名。一旦进"必需名单"并驱动修复，
    生成阶段就会**烧 token 去追幻影名字**——在按 `pass/CNY` 排序的赛制里等于直接扣分。
    所以：**告警（进 history / 日志），不进 findings、不驱动修复、不影响 passed**。

    判据口径（裁定原文）：散文式查"该名词是否出现在**某元素的可见文本或可访问名**里"——
    这里用源码文本近似（前端源码里出现过就算），比精确名宽得多。
    """
    findings: list[dict] = []
    front = output_dir / "frontend/src"
    if not front.is_dir():
        return findings
    blob = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                     for f in front.rglob("*") if f.is_file())
    low = blob.lower()
    for name in soft_names:
        if not name:
            continue
        if name.lower() not in low:
            findings.append({
                "kind": "accessible-name-soft-missing", "file": "frontend/src",
                "detail": f"散文靶子 {name!r} 在前端源码里没出现（**fail-soft：只告警，不驱动修复**）",
                "hint": "若需求确实要求这个控件，按最贴近的名字补上",
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


def check_db_tables(output_dir: Path) -> list[dict]:
    """**SQL 里用到的表，必须在后端代码里被 CREATE 过**（建在哪都行，但必须有）。

    为什么这条是**真判据**（2026-09-21 三组 artifact 对照实测）：

    | artifact | 模型 | 仓储里有没有 `CREATE TABLE` | 结果 |
    |---|---|---|---|
    | M3b-0 | `deepseek-chat` | ✅ 有（`auth_repository.js`） | **6/6** |
    | 标定组 | `deepseek-v4-flash` | ❌ 没有 | 3/6 |
    | 闸门 0 组 | `deepseek-v4-flash` | ❌ 没有 | 3/6 |

    两个 flash 产物的后端里，`CREATE TABLE` **只出现在模板自己的注释里** →
    注册接口 `SELECT … FROM users` 抛 `SQLITE_ERROR: no such table` →
    要么被 route 捕获成 500（用户名永远不出现 → 三条判据挂），
    要么以未捕获 rejection 把 Node 打死（`glm-5.3-flash` 那次，后面 5 条全 `ERR_CONNECTION_REFUSED`）。

    ⚠️ **教训（记下来免得再犯）**：我先做过这版检查，然后因为**更严的"必须建在 init_db.js 里"那版
    误报了 M3b-0**（它建在仓储模块里、照样通过），就把整个检查撤了。
    **撤错了**——放宽到"建在后端任何地方都算"就能正确区分三组。**一条检查误报，应该先放宽判据，
    而不是删掉它。**

    例外：`test_harness.js` 自建 `:memory:` 库，它的 SQL 不算。
    """
    findings: list[dict] = []
    backend = output_dir / "backend/src"
    if not backend.is_dir():
        return findings
    re_from = re.compile(r"(?:FROM|INTO|UPDATE)\s+[`\"\[]?(\w+)", re.I)
    created: set[str] = set()
    used: set[tuple[str, str]] = set()
    for f in sorted(backend.rglob("*.js")):
        if f.name == "test_harness.js":
            continue
        rel = f.relative_to(output_dir).as_posix()
        text = strip_js_comments(f.read_text(encoding="utf-8", errors="replace"))
        created |= {m.group(1).lower() for m in RE_CREATE_TABLE.finditer(text)}
        used |= {(m.group(1).lower(), rel) for m in re_from.finditer(text)}
    noise = {"select", "1", "sqlite_master"}
    # ⚠️ `used` 是 (表名, 文件) 元组集合，`created` 只有表名——**不能直接相减**
    # （元组集合 − 名字集合 = 永远不消掉任何元素，于是每个产物都报同样的几条）。
    # 计划把这条定位出来了；这个 bug 让闭环一直在修「幻影」：每条假发现 = 一次全量重发。
    for table, rel in sorted((t, r) for t, r in used if t not in created):
        if table in noise or table.startswith("sqlite_"):
            continue
        findings.append({
            "kind": "table-not-created", "file": rel,
            "detail": f"SQL 里用到了表 `{table}`，但后端**没有任何地方 CREATE 它** "
                      "（会抛 SQLITE_ERROR：route 里被吞成 500，未捕获时直接打死 Node）",
            "hint": f"把 `CREATE TABLE IF NOT EXISTS {table} (…)` 加进 `{SCHEMA_REL}`"
                    "（管线会把它注入到后端启动流程里执行）——"
                    "**不要**写在 JS 里，也不要改 `init_db.js`",
        })
    return findings


def check_schema_injected(output_dir: Path) -> list[dict]:
    """`schema.sql` 里的表，必须真的进了 `init_db.js` 的注入块。

    为什么单列一条（而不是并进 `check_db_tables`）：注入是**管线的确定性动作**，
    它唯一会静默失效的方式是 `init_db.js` 被整体重写（越界写入或人工手改）→
    三个锚点全不命中，而 `check_db_tables` 只看到"SQL 里用到、JS 里没 CREATE"这个
    结果，看不出原因。这条把原因直接指出来，而且它的修法是**零 token 的管线动作**
    （`verify/loop.py` 收到这个 kind 就直接重注入，不叫模型）。
    """
    schema_path = output_dir / SCHEMA_REL
    init_path = output_dir / INIT_DB_REL
    if not schema_path.is_file() or not init_path.is_file():
        return []
    statements = read_schema_statements(output_dir)
    tables = sorted({m.group(1).lower() for m in RE_CREATE_TABLE.finditer("\n".join(statements))})
    if not tables:
        return []
    text = init_path.read_text(encoding="utf-8", errors="replace")
    if BEGIN_MARK not in text:
        return [{
            "kind": "schema-not-injected", "file": INIT_DB_REL,
            "detail": f"`{SCHEMA_REL}` 定义了 {len(tables)} 张表，但 `{INIT_DB_REL}` 里"
                      "没有管线的注入块——启动时不会建表（注册 500 / 未捕获时直接把 Node 打死）",
            "hint": "重跑建表落位（管线确定性动作，无需模型）",
        }]
    injected = {m.group(1).lower() for m in RE_CREATE_TABLE.finditer(text)}
    missing = [t for t in tables if t not in injected]
    if missing:
        return [{
            "kind": "schema-not-injected", "file": INIT_DB_REL,
            "detail": f"`{SCHEMA_REL}` 里有 {len(missing)} 张表没进注入块：{missing}"
                      "（多半是 schema 改了但没重新注入）",
            "hint": "重跑建表落位（管线确定性动作，无需模型）",
        }]
    return []


def check_module_system(output_dir: Path) -> list[dict]:
    """后端 `.js` **不许用 ESM 语法**——模板是 CommonJS（`package.json` 没有 `"type": "module"`）。

    为什么这条是**真判据**（2026-09-21 闸门第 0 条 run d 实测）：模型给
    `backend/src/auth/auth_repository.js` 写了 `import { run, get } from '../database/index.js'`，
    而脚手架 `database/index.js` 是 `module.exports = {…}` →
    Node 直接
    `SyntaxError: The requested module '../database/index.js' does not provide an export named 'get'`
    → **后端进程起不来**（node 立刻退出）→ 冒烟 `GET /` 连不上 → 判据 0/6。
    这一条的形态是"应用一起来就崩"，不是"功能没做对"，而且是**静态可判**的。
    （那次闭环的审查**发现了**这个缺陷，但修复改的是 `app.js`——症状文件 vs 根因文件分家了；
    所以这条检查的价值就是**在生成阶段直接点名那个真正要改的文件**。）
    """
    findings: list[dict] = []
    pkg = output_dir / "backend/package.json"
    if pkg.is_file():
        try:
            if (json.loads(pkg.read_text(encoding="utf-8")).get("type") == "module"):
                return findings          # 真是 ESM 工程，不拦
        except Exception:  # noqa: BLE001
            pass
    backend = output_dir / "backend/src"
    if not backend.is_dir():
        return findings
    for f in sorted(backend.rglob("*.js")):
        rel = f.relative_to(output_dir).as_posix()
        text = strip_js_comments(f.read_text(encoding="utf-8", errors="replace"))
        if not (RE_ESM_IMPORT.search(text) or RE_ESM_EXPORT.search(text)):
            continue
        findings.append({
            "kind": "module-system-mismatch", "file": rel,
            "detail": f"这个文件用了 ESM 语法（import/export），但后端是 **CommonJS** 工程"
                      "（package.json 没有 type=module，脚手架用 require/module.exports）→ "
                      "Node 启动时直接 SyntaxError/ExportError，**后端起不来**",
            "hint": "把本文件改成 CommonJS：`const { run, get } = require('../database');` + "
                    "`module.exports = { … }`（脚手架导出的是 CommonJS，import 拿不到具名导出）",
        })
    return findings


def check_syntax_balance(output_dir: Path) -> list[dict]:
    """括号/引号配平（词法级）——抓"少一个 `)`"这类会让构建或启动**直接失败**的错误。

    为什么它是**闸门上的洞的补丁**（2026-09-21 run e 实测）：闭环把 `app.js` 里的 `});`
    写成 `}` → `SyntaxError: missing ) after argument list` → 后端起不来 → 冒烟就挂，
    那 23.4 万 token 的产物在判据侧归零。而 L1 原有 6 项检查（import / 路由 / 可访问名 /
    建表 / 脚手架 / 脚本）**都不看语法**，模型自检也没抓到。

    假阳性实测（2026-09-21）：官方模板 + 6 份历史产物 ≈108 个文件 → **0 个问题**；
    run e 那份坏产物 → 精确命中 `backend/src/app.js`（少一个 `)`）。
    """
    findings: list[dict] = []
    for top in ("frontend/src", "backend/src"):
        base = output_dir / top
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not (f.is_file() and f.suffix in (".js", ".jsx", ".ts", ".tsx")):
                continue
            probs = scan(f.read_text(encoding="utf-8", errors="replace"))
            if probs:
                rel = f.relative_to(output_dir).as_posix()
                findings.append({
                    "kind": "syntax-unbalanced", "file": rel,
                    "detail": f"括号/引号不配平（{probs[0]}；共 {len(probs)} 处）——"
                              "这类错误会让构建或后端起不来，不是「功能没做对」",
                    "hint": "与模板对应文件逐段对照，补回缺失的 `)` / `}` / 反引号",
                })
    return findings


def check_aria_name_sources(output_dir: Path) -> list[dict]:
    """控件**必须有真正的 ARIA 名来源**——`<meter>` 缺 `aria-label` 是实测过的那一类。

    为什么把它从 prompt 硬清单**下沉成静态检查**（`PLAN.md` §7 的纪律：
    "能用零 token 静态判的规则，不许用生成去验"）：
    - 已有的 `check_accessible_names` 只查"需求声明的名字在源码里出现过"——
      `<label htmlFor="passwordStrength">密码强度</label>` 让字符串出现、检查通过，
      而**元素的 ARIA 名仍然是空的**。
    - 实测（闸门第 0 条 run c，探针 `output/lab04run/probe_meter.js`）：`<meter>` 只靠
      `<label htmlFor>` 时 `getByRole('meter', {name:/密码强度/})` 命中 **0**（`getByLabel` 命中 1）；
      补 `aria-label` 后命中 1，判据 5/6 → **6/6**。
    - → 这条规则此前只写在生成/审查/修复三处 prompt 里（"靠模型记得"），现在**机器也会拦**。

    范围**刻意窄**：只查 `<meter>`（有实测证据的那一个）。`<select>`/`<input>` 靠
    `<label htmlFor>` 能正常得到 ARIA 名——**误报比漏报贵**（每条假发现 = 一轮全量修复）。
    """
    findings: list[dict] = []
    front = output_dir / "frontend/src"
    if not front.is_dir():
        return findings
    for f in sorted(front.rglob("*")):
        if not (f.is_file() and f.suffix in (".ts", ".tsx", ".js", ".jsx")):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in RE_METER.finditer(text):
            head = m.group(0)                       # `<meter` … 它的第一个 `>`
            if "aria-label" in head or "aria-labelledby" in head:
                continue
            rel = f.relative_to(output_dir).as_posix()
            findings.append({
                "kind": "meter-without-aria-label", "file": rel,
                "detail": f"第 {text[:m.start()].count(chr(10)) + 1} 行的 `<meter>` 没有 "
                          "`aria-label`/`aria-labelledby` —— 只写 `<label htmlFor>` 时它的 "
                          "**ARIA 可访问名是空的**：`getByLabel` 找得到，但 "
                          "`getByRole('meter', {name})` **找不到**（实测会直接掉一条判据）",
                "hint": "给这个 `<meter>` 加 `aria-label=\"密码强度\"`（`<label>` 保留，两者都要）",
            })
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
           template_dir: Path, soft_names: list[str] | None = None, log=print) -> dict:
    """跑完整 L1，返回 {passed, findings}。findings 可**直接**喂给定向修复。"""
    findings: list[dict] = []
    checks = [
        ("imports", lambda: check_imports(output_dir)),
        ("routes_links", lambda: check_routes_and_links(output_dir, requirement_brief)),
        ("accessible_names", lambda: check_accessible_names(output_dir, required_names)),
        ("prose_names", lambda: check_prose_names(output_dir, soft_names or [])),
        ("db_tables", lambda: check_db_tables(output_dir)),
        ("schema_injected", lambda: check_schema_injected(output_dir)),
        ("module_system", lambda: check_module_system(output_dir)),
        ("syntax_balance", lambda: check_syntax_balance(output_dir)),
        ("aria_name_sources", lambda: check_aria_name_sources(output_dir)),
        ("scaffold", lambda: check_scaffold_intact(output_dir, template_dir)),
        ("scripts", lambda: check_scripts(output_dir)),
    ]
    per_check: dict[str, int] = {}
    soft: list[dict] = []
    for name, fn in checks:
        try:
            got = fn()
        except Exception as exc:  # noqa: BLE001 —— 检查器自己崩了也要报出来
            got = [{"kind": "checker-crashed", "file": name, "detail": repr(exc), "hint": "修检查器"}]
        # 散文靶子的缺失是**告警**：不进 findings（因此不驱动修复、不影响 passed），但必须可见
        hard = [f for f in got if not str(f.get("kind", "")).endswith("-soft-missing")]
        soft += [f for f in got if str(f.get("kind", "")).endswith("-soft-missing")]
        per_check[name] = len(hard)
        findings.extend(hard)

    log("  L1 闸门：")
    for name, n in per_check.items():
        log(f"    {'✅' if n == 0 else '❌'} {name:18s} {n} 项")
    for f in findings[:12]:
        log(f"      · [{f['kind']}] {f['file']}: {f['detail']}")
    if len(findings) > 12:
        log(f"      … 其余 {len(findings) - 12} 项")
    if soft:
        log(f"  ⚠️  散文靶子未兑现 {len(soft)} 条（fail-soft，不驱动修复）：")
        for f in soft[:6]:
            log(f"      · {f['detail'][:96]}")
    return {"passed": not findings, "findings": findings, "per_check": per_check,
            "soft_findings": soft}
