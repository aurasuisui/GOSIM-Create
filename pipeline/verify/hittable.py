"""**可命中性**：产物里哪些字符串真的能被 locator 打到（一处实现，两处消费）。

为什么要有这个模块（`PLAN.md` 裁决四，2026-09-23 拍板）：
判据要的是"**能被某个 locator 命中**"，而不是"源码里出现过这个字面串"。
`ctrip` 第二轮把 `'邮箱.*用户名.*手机号'`（**正则链的源码**）当"逐字硬名"注入后，
`l1.py` 的字面子串检查**永远追不到**它 —— 契约不可能满足 → 闭环 3 轮全 ❌、
**17,281 token = 该轮全程的 52%**（自检 3×12,326 + 定向修复 4×4,955），而产物里其实有
`<p>邮箱/用户名/手机号</p>`（**同一个元素、按序**），**判据的正则会命中**。

→ 所以 L1 对**正则体**改用本模块的"可命中性"判据，与 `eval/predict_judge.py`（③）**共用**同一实现
（纪律 14/18：静态优先、零 token；一份实现，别造第二份"契约的表示"）。

## 位置集合是**按族**的，而且**实测钉死过**（PLAN 裁决五）

成员资格**不靠推理**——用 `eval/locator_probe.js`（最小页面 + 每族 locator 各打一发）实测：

| 位置 | 命名族 `getByRole(8 role,{name})` ∪ `getByText` | 字段族 `getByLabel` ∪ `getByPlaceholder` ∪ `getByRole({textbox,…},{name})` |
|---|---|---|
| 文本节点（`<h1>probe</h1>` 这类） | ✅ | ❌（`getByLabel` **不认** `<p>`/`<label>` 的文本） |
| `aria-label` | ✅ | ✅ |
| `title` | ✅（可访问名的回退） | ✅（同上，走 textbox 的 name） |
| `placeholder` | ❌（`getByText` 不读它；`textbox` 不在 8 个 role 里） | ✅ |
| `name` 属性 | ❌ | ❌（**实测都不命中** —— 所以它**不是**位置，别当位置用） |

⚠️ 上一版把 `name` 当位置（`RE_ATTR_HITTABLE` 里的 `name` 分支）→ 那是**修松**：
一个只有 `name="..."` 的元素会被算成命中，而真判据打不到它。

## 「正则体」怎么认

`is_regex_body()`：含正则元字符（点 星 加 问 尖括号 竖线 方括号 圆括号 花括号 反斜杠）的契约名。
纯字面名（`Quick Guide` / `BookStack` / `footer`）**不含**元字符 → 仍走**字面子串**判据
（与今天**完全一致**，这是裁决四的验收第 3 条「纯字面金丝雀行为不变」）。
"""

from __future__ import annotations

import re

# 前端源码后缀（与 l1.py 的 FRONTEND_EXT 对齐；这里单列以免循环 import）
_FRONTEND_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".css", ".html"}

# 文本节点：`>文字<`（JSX 里最常见的那种；排除属性里的 `>`）
RE_TEXT_NODE = re.compile(r">\s*([^<>{}\n]{2,120}?)\s*<")
# 可访问名相关属性（**不含 `name`** —— 实测它不是任何一族的命中位置）
RE_ATTR_NAMED = re.compile(r"""(?:aria-label|title)\s*=\s*["']([^"']{1,120})["']""")
RE_ATTR_FIELD = re.compile(
    r"""(?:aria-label|placeholder)\s*=\s*["']([^"']{1,120})["']""")

_META = set(".*+?^$|[](){}\\")

NAMED = "named"
FIELD = "field"


def is_regex_body(name: str) -> bool:
    """这条契约名是不是**正则体**（含正则元字符）。纯字面名 → False。"""
    return bool(name) and any(ch in _META for ch in name)


def hittable_strings(blob: str, family: str | None = None) -> list[str]:
    """产物里**能被 locator 命中**的字符串。

    `family=None` → 两族的并集（③ 的默认口径，与它此前的行为一致）；
    `family=NAMED` / `family=FIELD` → 只用该族的位置集合（裁决五要求"按族取位置集合"）。
    """
    out = [m.group(1).strip() for m in RE_TEXT_NODE.finditer(blob)] if family in (None, NAMED) else []
    rxs = []
    if family in (None, NAMED):
        rxs.append(RE_ATTR_NAMED)
    if family in (None, FIELD):
        rxs.append(RE_ATTR_FIELD)
    for rx in rxs:
        out.extend((m.group(1) or "").strip() for m in rx.finditer(blob))
    return [s for s in out if s]


def matches(pattern: str, blob: str, family: str | None = None) -> bool:
    """`pattern`（正则体）能不能被某个可命中位置匹配（大小写不敏感 —— 与 ③ 的 `_hits` 同口径）。"""
    try:
        rx = re.compile(pattern, re.I)
    except re.error:
        rx = re.compile(re.escape(pattern), re.I)
    return any(rx.search(s) for s in hittable_strings(blob, family))


def frontend_blob(output_dir) -> str:
    """把一个应用目录的 `frontend/src` 合成一个字符串（L1 与 ③ 共用同一口径）。"""
    front = output_dir / "frontend" / "src"
    if not front.is_dir():
        return ""
    return "\n".join(f.read_text(encoding="utf-8", errors="replace")
                     for f in front.rglob("*")
                     if f.is_file() and f.suffix in _FRONTEND_SUFFIXES)
