"""骨架落地（管线第 3 阶段的最小版本）。

**为什么这一步比它看起来重要**：平台上每次 run 的第一道闸是
`web template is incomplete: expected frontend/ and backend/ directories`——
即"这次 run 必须留下一个 web 应用骨架"。实测（2026-09-20 V5 首次提交）：
只跑第 1 阶段（需求编译）的 agent 六个任务全挂在这一条上，
**连"跑完"都做不到**，而"跑得完"是计分链条的第 1 环（`PLAN.md` §1）。

所以这里先做**确定性的模板落地**：把官方 `web-react-express/` 骨架的**内容**
拷进输出目录，让 `frontend/` 与 `backend/` 出现在输出根。
这是 `PLAN.md` §4.3 "降级模式：保证产出可构建可运行的最小应用" 的第一步实现。

后续阶段（设计 / 实现生成）再往这个骨架上填东西，不改这里的契约。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

TEMPLATE_ID = "web-react-express"


class ScaffoldError(RuntimeError):
    """模板找不到或拷贝失败——这是**必须报出来**的错误，不能静默跳过。"""


def find_template_root() -> Path | None:
    """按优先级找模板根目录（模板根下应有 `web-react-express/`）。

    优先级说明：
    1. `ARC_AGENT_TEMPLATES_ROOT` —— ARC 自己的环境变量（`app_type_handler/base.py:18`）。
       平台若设了它，跟着走最稳。
    2. `ARCBENCH_TEMPLATE_DIR` —— 平台可能用这个传模板位置。
    3. 包内默认 `templates/`：本文件在 `<根>/generate/scaffold.py`，
       所以 `parents[1]` 就是"含 templates/ 的那一层"——
       **工作区布局**下是 `pipeline/`（→ `pipeline/templates/`），
       **zip 布局**下就是包根（→ `templates/`）。两种布局同一条算式。
    """
    candidates: list[Path] = []
    for var in ("ARC_AGENT_TEMPLATES_ROOT", "ARCBENCH_TEMPLATE_DIR"):
        raw = os.environ.get(var, "").strip()
        if raw:
            candidates.append(Path(raw).expanduser())
    candidates.append(Path(__file__).resolve().parents[1] / "templates")

    for root in candidates:
        if (root / TEMPLATE_ID).is_dir():
            return root
    return None


def has_existing_app(output_dir: Path) -> bool:
    """输出目录里是否已经有一个应用骨架。

    有就**不覆盖**——这同时兼顾两件事：
      · 平台侧"从头编译"时输出目录是空的（照常拷模板）；
      · 决赛的 evolution 模式会把上一版应用放进来（ARC `base.py:64-68` 那种情形），
        此时覆盖等于把 baseline 删掉。
    """
    return (output_dir / "frontend").is_dir() and (output_dir / "backend").is_dir()


# ---- 已知上游 bug 的确定性修补 --------------------------------------------------
# 为什么要有这一段：模板是**上游产物**，我们把 bug 一起复制过来了。
# 一旦发现问题，修复要落在**产物**上（而不是只改我们包里的副本）——因为
# 脚手架优先用平台自带的模板（`ARC_AGENT_TEMPLATES_ROOT`），只改自己的副本修不到平台那份。
#
# 每一条都要求：可判定的模式、幂等、修复后日志留痕。
KNOWN_FIXES: list[dict] = [
    {
        "file": "backend/src/database/init_db.js",
        "why": "上游 ARC 模板 bug：`initializeDatabase()` 第二次调用起返回 `initPromise`，"
               "而它 resolve 成 undefined（IIFE 没有 return）→ `db_runtime.js` 的 "
               "`run/get/all/exec/withTransaction` 全部拿到 undefined 并崩。"
               "复现：`app.js` 启动时先调一次 initializeDatabase()（模板自己写的），"
               "之后任何一次查询助手调用都会 TypeError: Cannot read properties of undefined (reading 'exec')。",
        "buggy": "     */\n  })();",
        "fixed": "     */\n    return database;\n  })();",
    },
]


def apply_known_fixes(output_dir: Path, *, log=print) -> list[dict]:
    """把已知上游 bug 的修补应用到**产物**上。幂等：模式不匹配就跳过。"""
    applied: list[dict] = []
    for fix in KNOWN_FIXES:
        target = output_dir / fix["file"]
        if not target.is_file():
            continue
        text = target.read_text(encoding="utf-8")
        if fix["fixed"] in text:
            continue                      # 已经修过（或上游自己修了）
        if fix["buggy"] not in text:
            log(f"  ⚠️  已知修复的模式没命中（模板版本变了？）：{fix['file']}")
            continue
        target.write_text(text.replace(fix["buggy"], fix["fixed"], 1), encoding="utf-8")
        applied.append({"file": fix["file"], "why": fix["why"]})
        log(f"  🔧 修补上游 bug：{fix['file']}")
        log(f"      原因：{fix['why']}")
    return applied


def scaffold_app(output_dir: Path, *, log=print) -> dict:
    """把模板内容拷进 `output_dir`，返回摘要字典。"""
    root = find_template_root()
    if root is None:
        raise ScaffoldError(
            "找不到 web 模板：ARC_AGENT_TEMPLATES_ROOT / ARCBENCH_TEMPLATE_DIR 都没指向"
            "含 web-react-express/ 的目录，包内 templates/ 也不在。"
        )
    template_dir = root / TEMPLATE_ID

    if has_existing_app(output_dir):
        log(f"  输出目录已有应用骨架（{output_dir}），跳过模板落地"
            "（evolution 语义：保留现有应用作为基线）")
        return {"scaffolded": False, "reason": "existing-app", "template_dir": str(template_dir)}

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(template_dir, output_dir, dirs_exist_ok=True)
    except Exception as exc:  # noqa: BLE001 —— 拷不动必须让平台上看见
        raise ScaffoldError(f"模板拷贝失败 {template_dir} → {output_dir}：{exc!r}") from exc

    log(f"  模板已落地：{template_dir} → {output_dir}"
        f"（frontend/ + backend/ 齐备）")
    fixes = apply_known_fixes(output_dir, log=log)
    return {
        "scaffolded": True,
        "template_dir": str(template_dir),
        "output_dir": str(output_dir),
        "known_fixes_applied": fixes,
    }
