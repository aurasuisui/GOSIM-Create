"""把 web 模板落进输出目录（frontend/ + backend/），并修模板自带的已知 bug。

为什么必须有这一步（平台实测）：平台的**第一道闸**是"输出目录里要有 frontend/ 与 backend/"
（缺了报 web template is incomplete: expected frontend/ and backend/ directories，任务一起挂）。

模板来源优先级：
  1. 平台自己给的（ARC_AGENT_TEMPLATES_ROOT / ARCBENCH_TEMPLATE_DIR）—— 那是它会构建/起服务的那一份；
  2. 随包携带的副本 g2/templates/web-react-express（本地开发用，**已带修复**）。

本模块的修复是"模式保护 + 幂等"的：模式不命中就告警跳过，绝不盲替换。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

TEMPLATE_ID = "web-react-express"


def bundled_template_root() -> Path:
    """随包携带的模板根（= g2/templates，里头有 web-react-express/）。"""
    g2_root = Path(__file__).resolve().parents[2]
    return g2_root / "templates"


def find_template_root() -> Path | None:
    for var in ("ARC_AGENT_TEMPLATES_ROOT", "ARCBENCH_TEMPLATE_DIR"):
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        root = Path(raw)
        if (root / TEMPLATE_ID).is_dir():
            return root
        if root.name == TEMPLATE_ID and root.is_dir():
            return root.parent
    b = bundled_template_root()
    return b if (b / TEMPLATE_ID).is_dir() else None


def has_app(output_dir: Path) -> bool:
    return (output_dir / "frontend").is_dir() and (output_dir / "backend").is_dir()


# ---------------------------------------------------------------- 上游模板 bug
# 症状：initializeDatabase() 的**后续调用**返回 initPromise，而那个 promise resolve 成
#       `database` 之外的东西（IIFE 没有 return）→ db_runtime 的 run/get/all/exec 全拿到 undefined，
#       于是"第一次查询就崩"。app.js 启动时自己先调了一次，所以任何一次助手调用都会中招。
# 修法：把 IIFE 的返回值显式 resolve 成 database，并给 initPromise 一个 then 快照。
FIX_TEMPLATE = """  initPromise = (async () => {{
    await runStatement(database, 'PRAGMA foreign_keys = ON;');
{body}
    // 建表与种子由管线确定性生成（schema.sql / seed.sql）——见 database/bootstrap.js
    try {{
      const {{ bootstrap }} = require('./bootstrap');
      await bootstrap(database);
    }} catch (error) {{
      console.error('[bootstrap] failed:', error);
    }}
  }})().then(() => database);"""


BOOTSTRAP_CALL = """    // 建表与种子由管线确定性生成（schema.sql / seed.sql）——见 database/bootstrap.js
    try {
      const { bootstrap } = require('./bootstrap');
      await bootstrap(database);
    } catch (error) {
      console.error('[bootstrap] failed:', error);
    }"""


def patch_init_db(path: Path) -> bool:
    """两处修复（幂等、模式保护）：

      ① `initializeDatabase()` 的后续调用返回 `initPromise`，而它 resolve 成 `database` 之外的东西，
         导致 db_runtime 的 run/get/all/exec 全拿到 undefined（"第一次查询就崩"）；
      ② 在同一个 IIFE 里**执行 schema.sql 与 seed.sql** —— 建表与种子由管线确定性生成，
         不能指望模型写（实测模型既不建表也不写种子，而判据依赖既有记录）。
    返回是否改过文件。
    """
    text = path.read_text(encoding="utf-8")
    if "bootstrap(database)" in text:
        return False                                   # 幂等：两处都已在位
    m = re.search(r"  initPromise = \(async \(\) => \{\n(.*?)\n  \}\)\(\);\n", text, re.S)
    if m:
        body = m.group(1)
        new = FIX_TEMPLATE.format(body=body)
        text = text[:m.start()] + new + "\n" + text[m.end():]
    patched = bool(m)
    # ② 注入 bootstrap（在 IIFE 里、PRAGMA 之后）
    if "bootstrap(database)" not in text:
        m2 = re.search(r"( +await runStatement\(database, 'PRAGMA foreign_keys = ON;';\)\n)", text)
        if m2:
            indent = " " * (len(m2.group(1)) - len(m2.group(1).lstrip(" ")))
            call = "\n".join(indent + l.lstrip() if l.strip() else l for l in BOOTSTRAP_CALL.splitlines())
            text = text[:m2.end()] + call + "\n" + text[m2.end():]
            patched = True
    if patched:
        path.write_text(text, encoding="utf-8")
    return patched


def apply_known_fixes(output_dir: Path, *, log=print) -> list[dict]:
    applied: list[dict] = []
    target = output_dir / "backend/src/database/init_db.js"
    if not target.is_file():
        log("  ⚠️  模板修复跳过：找不到 backend/src/database/init_db.js")
        return applied
    if patch_init_db(target):
        applied.append({"file": "backend/src/database/init_db.js",
                        "reason": "上游模板 initializeDatabase 的后续调用 resolve 成 undefined"})
        log("  🔧 修补上游模板 bug：backend/src/database/init_db.js（initializeDatabase 返回值）")
    else:
        log("  ✓ 模板修复无需改动（已在位或上游已改）")
    return applied


def scaffold_app(output_dir: Path, *, log=print) -> dict:
    root = find_template_root()
    if root is None:
        raise RuntimeError("找不到 web 模板：ARC_AGENT_TEMPLATES_ROOT / ARCBENCH_TEMPLATE_DIR 都没指向它"
                           "，随包副本也不在")
    src = root / TEMPLATE_ID
    if not src.is_dir():
        raise RuntimeError(f"模板目录不存在：{src}")
    if has_app(output_dir):
        log(f"  骨架已存在（frontend/ + backend/ 齐备）：{output_dir}")
    else:
        shutil.copytree(src, Path(output_dir), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("node_modules", "dist", ".git"))
        log(f"  模板已落地：{src} → {output_dir}（frontend/ + backend/ 齐备）")
    fixes = apply_known_fixes(Path(output_dir), log=log)
    return {"template": str(src), "fixes": fixes}