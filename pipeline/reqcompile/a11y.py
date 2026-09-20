"""可访问名契约抽取。

## 为什么这件事是整条管线里杠杆最高的（`PLAN.md §4.1`）

`arc-bench/scripts/audit-benchmark-tests.js` 的 `forbiddenSpecPatterns` 禁止
外部测试使用：`page.goto` / `page.request` / `/api/` / `localStorage` /
`.evaluate` / `.route` / `toHaveURL` / `page.url` / class-id-data 选择器。

**禁完之后，外部测试只剩三种手段**：点击导航、按可访问名/role 定位、
断言可见文本。所以：

> 考的不是功能存不存在，是生成的应用有没有暴露出稳定的
> accessible name / role 结构。

于是"需求文档里点名的那些名字"就是生成阶段必须逐个兑现的清单。

## 实测：表述方式不统一（这是本模块的设计约束）

对六个赛题抽样后的真实句式：

    12306        input fields labeled "Name", "Passport number", ...
                 the "Register" button                 （"X" <role> 是主流，210 处）
    bookstack    enters ... in the "Email address" field
                 clicks `New Shelf` / `Login`          （反引号 = 可点击）
    prestashop   Click "Sign in" link / "Clear all" button
    stackoverflow  "Log in" submit button, "Forgot password?" link
    ctrip        带中文（989 个汉字），另有 labeled 2 处
    quickstart   `用户名` 为必填文本输入框 …        （中文反引号 + 为…输入框）

**没有任何一种句式覆盖全部六个。** 所以这里用**模式表**而不是写死逻辑：
每种句式一条规则，新增 app 只需加规则，不动代码。

## 设计原则

- **宁可多收，不可漏收**：多收一个名字，生成时多渲染一个 label（廉价）；
  漏收一个，那一条外部测试直接找不到元素（昂贵）。
- **每条都留证据**：记住是从哪个节点的哪段文本捞出来的，方便人工核对。
- 输出同时供三处用：① 写进生成 prompt 的必做清单；
  ② 本地静态检查（扫 JSX 是否每个交互元素都有 label/role）；
  ③ 自动化探针（爬一遍应用验证名字存在）。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .model import RequirementTree

# ---------- 角色词表 ----------
# 英文角色 → 归一化 role（对应 ARIA / Playwright 的 getByRole）
ROLE_WORDS: dict[str, str] = {
    "button": "button",
    "link": "link",
    "field": "textbox",
    "input": "textbox",
    "textbox": "textbox",
    "text box": "textbox",
    "dropdown": "combobox",
    "combo box": "combobox",
    "combobox": "combobox",
    "select": "combobox",
    "checkbox": "checkbox",
    "radio": "radio",
    "tab": "tab",
    "menu": "menu",
    "menuitem": "menuitem",
    "option": "option",
    "heading": "heading",
    "header": "heading",
    "dialog": "dialog",
    "search box": "searchbox",
    "searchbox": "searchbox",
}

# 中文角色词 → 归一化 role
ROLE_WORDS_ZH: dict[str, str] = {
    "文本输入框": "textbox",
    "密码输入框": "textbox",
    "输入框": "textbox",
    "文本框": "textbox",
    "下拉选择框": "combobox",
    "下拉框": "combobox",
    "原生下拉": "combobox",
    "选择框": "combobox",
    "复选框": "checkbox",
    "多选框": "checkbox",
    "单选框": "radio",
    "按钮": "button",
    "链接": "link",
    "标签页": "tab",
    "菜单": "menu",
}

# 引号内的名字。成对匹配 "" / `` / “” / ‘’
QUOTED = re.compile(
    r'"([^"\n]{1,60})"'          # "X"
    r"|`([^`\n]{1,60})`"          # `X`
    r"|\u201c([^\u201d\n]{1,60})\u201d"   # “X”
    r"|\u2018([^\u2019\n]{1,60})\u2019"   # ‘X’
)

# 明确的可点击动词（后面跟的引号内容是交互元素）
CLICK_VERBS = re.compile(
    r"\b(?:clicks?|clicking|taps?|presses?|selects?|choose|chooses|opens?|"
    r"submits?|toggles?|checks?|unchecks?|点击|单击|选择|打开|提交|勾选)\b",
    re.I,
)

# "labeled "X"" —— 注意实际文本里几乎从不是单个：
#   input fields labeled "Name", "Passport number", "Username", and "Email address"
#   date pickers labeled "Passport expiration date" and "Date of birth"
LABELED = re.compile(r"labeled\b", re.I)

# 列表分隔符（英文逗号/and/or + 中文顿号）
LIST_SEP = re.compile(r"\s*(?:,|;|\band\b|\bor\b|\u3001)\s*", re.I)

# 锚点与其后引号之间允许的填充词数量和长度（"fields" / "for a" 之类）
MAX_FILLER = 24

# 中文契约句：「必须提供以下稳定且唯一的中文可访问名称」
ZH_CONTRACT_HINT = re.compile(r"可访问名称|accessible name|getByLabel|getByRole", re.I)

# 中文契约的一条：`名字` 为/是 <描述（到下一个分号/句号为止）>
# 描述段截到 300 字符：够装下「必填密码输入框，并显示随输入动态变化的
# 三段密码强度指示器」这类长尾，又不会把整段 description 吞进来。
ZH_CLAUSE = re.compile(r"`([^`\n]{1,60})`\s*(?:\u4e3a|\u662f)([^\uff1b;\u3002\n]{0,300})")


def _consume_quoted_list(text: str, pos: int) -> list[tuple[str, int, int]]:
    """从 pos 起吃一串引号名（逗号 / and / or / 顿号 分隔）。

    这是本抽取器最关键的一个函数：实测里绝大多数名字是**成串出现的**，
    只抓第一个会漏掉大半（12306 上覆盖率从 70% 卡住的原因）。
    """
    out: list[tuple[str, int, int]] = []
    i = pos
    while True:
        m = QUOTED.match(text, i)
        if not m:
            break
        name = next((g for g in m.groups() if g), None)
        if name is None or not name.strip():
            break
        out.append((name.strip(), m.start(), m.end()))
        i = m.end()
        sep = LIST_SEP.match(text, i)
        if not sep:
            break
        i = sep.end()
    return out


def _role_from_words(window: str) -> str:
    """从一小段文本里认角色词。返回归一化 role，认不出返回 ""。"""
    low = window.lower()
    for word, role in sorted(ROLE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            return role
    return ""


@dataclass
class AccessibleName:
    """一个必须存在的可访问名。"""

    name: str
    role: str                     # 归一化后的 role，未知记 ""
    req_id: str
    required: bool = False        # 中文件式能读出"必填/可选"
    options: list[str] = field(default_factory=list)  # 下拉框的选项
    pattern: str = ""             # 命中哪条规则（便于回归）
    evidence: str = ""            # 原文片段，人工核对用

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "req_id": self.req_id,
            "required": self.required,
            "options": list(self.options),
            "pattern": self.pattern,
            "evidence": self.evidence[:200],
        }


@dataclass
class AccessibleNameIndex:
    entries: list[AccessibleName] = field(default_factory=list)

    def by_req(self) -> dict[str, list[AccessibleName]]:
        out: dict[str, list[AccessibleName]] = defaultdict(list)
        for e in self.entries:
            out[e.req_id].append(e)
        return dict(out)

    def unique_names(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for e in self.entries:
            if e.name not in seen:
                seen.add(e.name)
                out.append(e.name)
        return out

    def role_counts(self) -> dict[str, int]:
        return dict(Counter(e.role or "unknown" for e in self.entries).most_common())

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.entries),
            "unique_names": len(self.unique_names()),
            "role_counts": self.role_counts(),
            "entries": [e.to_dict() for e in self.entries],
        }


def _iter_quoted(text: str):
    """产出 (名字, 起止位置)。同时覆盖 " / ` / “” / ‘’ 四种引号。"""
    for m in QUOTED.finditer(text):
        name = next((g for g in m.groups() if g), None)
        if name:
            yield name.strip(), m.start(), m.end()


def _role_near(tail: str, head: str) -> tuple[str, str]:
    """在引号前后找角色词。返回 (归一化 role, 命中的原文词)。

    先看后面（`"X" button` 是最常见形态），再看前面（`only the "X" field`）。
    """
    window_after = tail[:40].lower()
    for word, role in sorted(ROLE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.match(r"[\s\-]" + re.escape(word) + r"\b", window_after):
            return role, word
    window_before = head[-40:].lower()
    for word, role in sorted(ROLE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"\b" + re.escape(word) + r"\s*$", window_before):
            return role, word
    return "", ""


def _extract_zh_contracts(text: str, req_id: str, index: AccessibleNameIndex) -> None:
    """抽中文契约句：「`名字` 为[必填/可选]…输入框」。

    实测教训：契约句里**第一个名字前面还有一大段引言**，例如

        ……必须提供以下稳定且唯一的中文可访问名称，供 Playwright 使用
        `getByLabel` 或 `getByRole` 定位，控件必须与对应 label 原生关联，
        不得依赖 CSS class、DOM 顺序或 placeholder：`用户名` 为必填文本输入框；
        `登录密码` 为必填密码输入框……

    所以**不能按 `；` 切句再要求每句以反引号开头**——那样会漏掉紧跟在
    冒号后面的第一个名字（`用户名`）。改成在整段里直接搜索模式。
    另外注意 `getByLabel` / `getByRole` 本身是反引号包着的，但后面跟的不是
    `为/是`，所以天然不会被误收。
    """
    for m in ZH_CLAUSE.finditer(text):
        name = m.group(1).strip()
        if not name:
            continue
        rest = m.group(2) or ""

        role = ""
        for word, r in sorted(ROLE_WORDS_ZH.items(), key=lambda kv: -len(kv[0])):
            if word in rest:
                role = r
                break

        required = bool(re.search(r"必填|必选|required", rest))

        options: list[str] = []
        if "选项包括" in rest or "选项有" in rest:
            segment = rest.split("选项包括", 1)[-1] if "选项包括" in rest \
                else rest.split("选项有", 1)[-1]
            segment = re.split(r"[；;]", segment, 1)[0]
            for nm in re.findall(r"[\u201c\"]([^\u201d\"]{1,30})[\u201d\"]", segment):
                nm = nm.strip()
                if nm and "初始值" not in nm:
                    options.append(nm)

        index.entries.append(AccessibleName(
            name=name, role=role, req_id=req_id, required=required,
            options=options, pattern="zh_backtick_contract",
            evidence=m.group(0).strip()[:200],
        ))


def extract_accessible_names(tree: RequirementTree) -> AccessibleNameIndex:
    """从需求树里抽可访问名契约。"""
    index = AccessibleNameIndex()

    for node in tree.ordered():
        # 本节点的可用文本：description + 场景名 + 场景步骤
        blobs: list[tuple[str, str]] = []
        if node.description:
            blobs.append(("description", node.description))
        for sc in node.scenarios:
            if sc.name:
                blobs.append(("scenario_name", sc.name))
            for st in sc.steps:
                if st.content:
                    blobs.append((f"scenario_step:{st.keyword}", st.content))

        for kind, text in blobs:
            _extract_from_text(text, node.id, kind, index)

    return index


def _extract_from_text(text: str, req_id: str, kind: str, index: AccessibleNameIndex) -> None:
    captured: set[tuple[int, int]] = set()   # 已收过的引号区间，避免重复

    def take(name: str, start: int, end: int, role: str, pattern: str,
             required: bool = False, options: list[str] | None = None) -> None:
        if (start, end) in captured:
            return
        if re.search(r"[/\\<>]|\.(png|jpe?g|ya?ml|ts|js|json|md)$", name, re.I):
            return                          # 路径/文件名，不是 UI 名字
        captured.add((start, end))
        index.entries.append(AccessibleName(
            name=name, role=role, req_id=req_id, required=required,
            options=options or [], pattern=pattern,
            evidence=text[max(0, start - 50):min(len(text), end + 50)].strip(),
        ))

    # ---- 规则 A：中文契约句 `X` 为……输入框（quickstart / ctrip 那种）----
    if ZH_CONTRACT_HINT.search(text) or ZH_CLAUSE.search(text):
        _extract_zh_contracts(text, req_id, index)

    # ---- 规则 B：`labeled` 锚点，吃后面的整串名字 ----
    # 四种形态都靠它：
    #   labeled "A", "B", "C"            → 角色取 labeled 前后的名词
    #   input fields labeled "A", "B"    → 角色 = textbox
    #   date pickers labeled "A" and "B" → 角色 = textbox
    #   labeled fields "A", "B"          → 角色取 labeled 与首个引号之间的词
    for m in LABELED.finditer(text):
        tail = text[m.end():]
        q = QUOTED.search(tail)
        if not q or q.start() > MAX_FILLER:
            continue
        between = tail[:q.start()]                      # labeled 与首个引号之间
        role = _role_from_words(between) or _role_from_words(text[max(0, m.start() - 40):m.start()])
        for name, s, e in _consume_quoted_list(text, m.end() + q.start()):
            take(name, s, e, role, "labeled_list")

    # ---- 规则 C：角色词在前，后跟一整串名字 ----
    #   the tabs "Popular", "ABCDE", "FGHIJ", and "UVWXYZ"
    #   Components: "Join Stack Overflow" header, "Log in" submit button
    #
    # 注意 `s?\b` 里的边界位置：写成 `\b` + word + `\bs?` 会让**复数永远匹配不上**
    # （`\btab\bs?` 要求 tab 后面先有个词边界，而 "tabs" 后面紧跟的是 s）。
    # 这个 bug 曾让 tabs/buttons/fields/options 这些复数锚点整批失效。
    for word, role in sorted(ROLE_WORDS.items(), key=lambda kv: -len(kv[0])):
        for m in re.finditer(r"\b" + re.escape(word) + r"s?\b", text, re.I):
            tail = text[m.end():]
            q = QUOTED.search(tail)
            if not q or q.start() > MAX_FILLER:
                continue
            for name, s, e in _consume_quoted_list(text, m.end() + q.start()):
                take(name, s, e, role, "role_before_list")

    # ---- 规则 D：兜底 —— 引号名字 + 邻近角色词 / 点击动词 ----
    for name, start, end in _iter_quoted(text):
        role, _word = _role_near(text[end:], text[:start])
        before = text[max(0, start - 45):start]
        clickable = bool(CLICK_VERBS.search(before))
        if not role and not clickable:
            continue
        take(name, start, end, role or ("button" if clickable else ""),
             "quoted_near_role" if role else "quoted_after_click_verb")


def required_names_for_prompt(index: AccessibleNameIndex, req_id: str) -> list[str]:
    """给生成 prompt 用的清单：本节点必须兑现的名字，按 role 分组。"""
    grouped: dict[str, list[str]] = defaultdict(list)
    for e in index.by_req().get(req_id, []):
        grouped[e.role or "unspecified"].append(e.name)
    lines: list[str] = []
    for role in sorted(grouped):
        names = sorted(set(grouped[role]))
        lines.append(f"  - {role}: " + ", ".join(repr(n) for n in names))
    return lines
