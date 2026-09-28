"""抽**带作用域的靶子**：getByRole(外层).getByRole(内层) 这类链。

为什么单列（实测）：keep 的判据大量用**作用域定位** ——
  getByRole(dialog, name=Note editor).getByRole(textbox, …)
  getByRole(complementary).getByRole(button, name=Work)
  getByRole(menu).getByRole(menuitem, name=Settings)
把名字平铺成一排 button 只能满足"名字存在"，满足不了"在某个容器里"。
所以壳层要按这些作用域**分组渲染**（dialog / complementary / menu / group …）。

实现：**不用一个大正则**（可读性差、出错难查）—— 先扫出所有 getByRole 调用，
再看相邻两次调用之间是不是只隔着一个点（允许 .first() 之类）。
"""
from __future__ import annotations

import re
from pathlib import Path

# ⚠️ 名字对象前面有**逗号**（`getByRole('button', { name: … })`）——
#    第一版漏了它，于是"带名字的调用"一个都没匹配上（`roles` 里只有 textbox/complementary 这种不带名字的）。
CALL = re.compile(r"getByRole\(\s*['\"]([a-z]+)['\"]\s*(?:,\s*\{([^{}]{0,160}?)\})?\s*\)", re.S)
NAME = re.compile(r"name:\s*/\^?([^/]{1,40}?)\$?/i")
LINKER = re.compile(r"^\s*(?:\.(?:first|last|nth)\([^)]*\)\s*)*\.\s*$")


ASSIGN = re.compile(
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[^;\n]{0,80}?getByRole\(\s*['\"]([a-z]+)['\"]\s*(?:,\s*\{([^{}]{0,160}?)\})?\s*\)",
    re.S)
VAR_USE = re.compile(r"\b([A-Za-z_$][\w$]*)\.getByRole\(\s*['\"]([a-z]+)['\"]\s*(?:,\s*\{([^{}]{0,160}?)\})?\s*\)", re.S)


def _variable_scopes(text: str) -> list[dict]:
    """`const dialog = page.getByRole(dialog, name=Note editor)` + `dialog.getByRole(textbox, …)`。

    为什么要这一遍：keep 的编辑器判据是**两行**写的（先取变量、再用变量找子控件），
    纯"相邻调用"扫不到 → 只扫相邻会漏掉这整类（本轮 keep 的最大失败类）。
    """
    out: list[dict] = []
    vars_: dict[str, tuple[str, str]] = {}
    for m in ASSIGN.finditer(text):
        vars_[m.group(1)] = (m.group(2), _name_of(m.group(3)))
    for m in VAR_USE.finditer(text):
        var = m.group(1)
        if var not in vars_:
            continue
        outer_role, outer_name = vars_[var]
        out.append({"outer_role": outer_role, "outer_name": outer_name,
                    "inner_role": m.group(2), "inner_name": _name_of(m.group(3))})
    return out

def _name_of(inner: str | None) -> str:
    if not inner:
        return ""
    m = NAME.search(inner)
    return m.group(1).strip() if m else ""


# `getByText(/X/i).first().getByRole(button, {name:/Y/i})` —— **按记录**定位的动作按钮。
# 实测这是 keep 最大的一类失败：判据先按**记录名**取到那个元素，再在它**里面**找动作按钮
# （More options / Archive / Change color / Close…）。所以每个记录元素里都要带这些按钮。
TEXT_CALL = re.compile(r"getByText\(\s*(?:/|['\"])(.{1,60}?)(?:/i|/|['\"])\s*\)", re.S)


def record_actions(tests_dir: Path) -> list[str]:
    """抽出"记录元素内部的动作按钮名"（去重、保序、剔掉数据值的名字）。"""
    actions: list[str] = []
    for p in sorted(Path(tests_dir).glob("*.ts")):
        text = p.read_text(encoding="utf-8", errors="replace")
        calls = [(m.end(), m.group(1)) for m in TEXT_CALL.finditer(text)]
        role_calls = [(m.start(), m.group(1), _name_of(m.group(2))) for m in CALL.finditer(text)]
        for end, _label in calls:
            for start, role, name in role_calls:
                if start < end or not LINKER.match(text[end:start]):
                    continue
                if role in {"button", "menuitem", "link", "tab"} and name and name not in actions:
                    actions.append(name)
    return actions

def has_record_scoping(tests_dir: Path) -> bool:
    """判据是否**按记录**定位动作按钮（`getByText(某记录)…getByRole(button, {name})`）。

    为什么要单独判一遍（实测代价 11 条判据）：把动作按钮塞进**每条记录**会让
    `getByRole(button, {name:/^Archive$/i})` 这类**全局**定位一次命中几十个 → strict mode violation。
    所以只有"判据真的按记录定位"时才启用（两个任务包实测一个 True 一个 False）。
    """
    # 实测判别：某个应用有 1 条这种链、另一个 0 条 → 用它当开关最省事，也最不容易误判。
    for p in sorted(Path(tests_dir).glob("*.ts")):
        if re.search(r"getByText\(.{0,200}?getByRole\(", p.read_text(encoding="utf-8", errors="replace"), re.S):
            return True
    return False


def record_scoped_actions(tests_dir: Path) -> list[str]:
    names: list[str] = []
    # ⚠️ 链**经常跨行**（`await page.getByText(...).first()` 换行再 `.getByRole(...)`）→
    #    逐行扫会全部漏掉（实测两个应用都返回空）。所以在**整份文件**上做正则。
    chain = re.compile(r"getByText\((.{0,160}?)\.getByRole\(\s*['\"](\w+)['\"]\s*(?:,\s*\{([^{}]{0,140}?)\})?", re.S)
    for p in sorted(Path(tests_dir).glob("*.ts")):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in chain.finditer(text):
            role = m.group(2)
            name = _name_of(m.group(3))
            if role in {"button", "menuitem", "link", "tab"} and name and name not in names:
                names.append(name)
    return names


def scoped_targets(tests_dir: Path) -> list[dict]:
    """返回 [{outer_role, outer_name, inner_role, inner_name}]（去重、保序）。"""
    out: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for p in sorted(Path(tests_dir).glob("*.ts")):
        text = p.read_text(encoding="utf-8", errors="replace")
        calls = [(m.start(), m.end(), m.group(1), _name_of(m.group(2))) for m in CALL.finditer(text)]
        for entry in _variable_scopes(text):
            key0 = (entry["outer_role"], entry["outer_name"], entry["inner_role"], entry["inner_name"])
            if key0 not in seen:
                seen.add(key0)
                out.append(entry)
        for i in range(len(calls) - 1):
            _s1, e1, outer_role, outer_name = calls[i]
            s2, _e2, inner_role, inner_name = calls[i + 1]
            if not LINKER.match(text[e1:s2]):
                continue
            key = (outer_role, outer_name, inner_role, inner_name)
            if key in seen:
                continue
            seen.add(key)
            out.append({"outer_role": outer_role, "outer_name": outer_name,
                        "inner_role": inner_role, "inner_name": inner_name})
    return out