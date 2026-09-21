"""§4.1 第 5 条规则：**散文名词枚举 + role 映射**（2026-09-21 补）。

## 为什么需要它

六个主赛道 app 里有四个是**散文式需求**：引号名极少，而"交互单元空转率"（场景步骤里有
click/select/enter/check/submit、却**拿不到任何 a11y 目标**的比例）实测为
**prestashop 50% · keep 40% · stackoverflow 29% · ctrip 18%**（12306 只有 2%，因为它自带显式契约）。
keep 那轮 31 条失败**全是"名字/角色对不上"**（`Toggle sidebar`×14 / `complementary`×7 / `Notes`×7 …）
——所以这条规则是 keep 通过数的**头号杠杆**，不只是闸门有话可说。

## 三个约束（`PLAN.md` §4.1 裁定，缺一不可）

1. **位置约束**：只抽**祈使宾语位 / 角色名词位**（`Click the X link` / `fill in the X field` /
   `the X button` / `Display X, Y, Z`），**不做无差别名词枚举**。
2. **必须同时报精度**：每 app 抽 20 条人工判（`eval/a11y_prose_probe.py` 会打出来）。
   只报召回一定虚高——地面真值里本身就混着非可访问名。
3. **散文式 app 的 L1 先 fail-soft**：告警进 `ValidationReport`，不判死（见 `verify/l1.py`）。

## 与引号式的关系

引号式（`"Save" button`）走原有规则、**精确名**判；散文式走本规则、**fail-soft**。
两类都进生成 prompt 的靶子清单，但**标注来源**——模型要知道哪些是"逐字照抄"、哪些是"散文抽取、可能不准"。

## ⚠️ 靶子只能从需求抽，不能从测试反推

平台上 agent **看不到测试**。测试只当**地面真值做校准**（`eval/a11y_prose_probe.py` 量召回），
不进任何抽取路径。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 角色名词映射

# 名词（单复数都收）→ ARIA role。`None` = "知道是个东西但不知道角色"（只带名字）。
ROLE_NOUNS: dict[str, str | None] = {
    "button": "button", "buttons": "button",
    "link": "link", "links": "link",
    "field": "textbox", "fields": "textbox", "input": "textbox", "inputs": "textbox",
    "textbox": "textbox", "textboxes": "textbox", "box": "textbox",
    "tab": "tab", "tabs": "tab",
    "checkbox": "checkbox", "checkboxes": "checkbox",
    "menu": "menu", "menus": "menu", "menuitem": "menuitem",
    "dialog": "dialog", "dialogs": "dialog", "modal": "dialog", "popup": "dialog",
    "editor": "dialog",
    "sidebar": "complementary", "panel": "complementary", "aside": "complementary",
    "navigation": "navigation", "nav": "navigation", "toolbar": "toolbar",
    "dropdown": "combobox", "combobox": "combobox", "select": "combobox",
    "listbox": "listbox",
    "icon": None, "icons": None, "chip": None, "chips": None, "badge": None,
}

# 纯 role 目标：需求里提到这些**结构**，但没给名字——判据可能只要 role 存在。
# keep 的 `getByRole('complementary')`×7 就是这一类（侧栏，不需要名字）。
PURE_ROLE_NOUNS: dict[str, str] = {
    "sidebar": "complementary", "side bar": "complementary",
    "navigation": "navigation", "nav bar": "navigation",
    "toolbar": "toolbar", "note editor": "dialog", "dialog": "dialog",
}

# 祈使动词：**必须带屈折变化**——踩过 `\bclick\b` 的坑（bookstack 的 `clicks the "Save" button`
# 匹配不上，交互单元从 32 掉到 6、空转率虚高到 83%；与 docs/06 §三的 `\btab\bs?` 同一个坑）。
VERB = (r"(?:click|clicks|clicking|tap|taps|tapping|press|presses|pressing|toggle|toggles|"
        r"open|opens|opening|close|closes|closing|select|selects|selecting|choose|chooses|"
        r"fill|fills|filling|enter|enters|entering|type|types|typing|submit|submits|"
        r"check|checks|checking|use|uses|using|view|views|display|displays|show|shows|"
        r"render|renders|list|lists|contain|contains|include|includes)")

# 位置约束（约束 1）：三种句式
RE_VERB_OBJ = re.compile(rf"\b{VERB}\s+(?:an|a|the)?\s*([^,.;:!?]{{2,48}})", re.I)
# ⚠️ 冠词交替**必须长在前**：写成 `(?:the|a|an)` 时 `a` 会先匹配，把 `an “X”` 吃成
# `n “X”`（实测 junk：`'n "Action undone" notification'` / `'n Undo notification'`）。
# 三处都得这么写。
RE_ROLE_NOUN_AFTER = re.compile(
    r"\b(?:an|a|the)\s+([A-Za-z][\w \-]{1,40}?)\s+(" + "|".join(map(re.escape, ROLE_NOUNS)) + r")\b",
    re.I)
RE_ROLE_NOUN_BEFORE = re.compile(
    r"\b(" + "|".join(map(re.escape, ROLE_NOUNS)) + r")\s+(?:named|called|labeled|labelled)\b", re.I)

# 质量过滤（约束 2 的第一道闸）：这些**不是**可访问名，是文本断言 / 测试数据 / 量词。
STOPWORDS = {
    "it", "them", "this", "that", "these", "those", "one", "ones", "all", "any", "some",
    "page", "app", "application", "screen", "view", "list", "notes", "note",  # 单独出现太泛（组合形态另算）
    "data", "content", "result", "results", "count", "number", "value", "values",
    "text", "label", "labels", "title", "name", "item", "items", "option", "options",
    "left", "right", "top", "bottom", "first", "last", "next", "previous",
    "notes list", "note list", "notes area", "deleted notes", "archived notes",
}
RE_HAS_DIGIT = re.compile(r"\d")
MAX_NAME_WORDS = 5
MAX_NAME_CHARS = 40

# 名词短语里**不允许出现**的功能词/动词——这是精度闸门。
# 实测（约束 2 的风险在这里兑现）：不加它时 keep 抽出 87 条，里面混着
# `'note when the'` / `'Trash view from the'` / `'page shows an open'` 这类**从句碎片**，
# 它们一旦进必需名单，生成阶段就会**烧 token 去追幻影名字**（而赛制按 pass/CNY 排序）。
BAD_WORDS = {
    "from", "to", "when", "after", "before", "in", "into", "on", "onto", "of", "and", "or",
    "the", "a", "an", "with", "for", "by", "at", "as", "is", "are", "was", "were", "be",
    "been", "being", "that", "which", "while", "then", "than", "it", "its", "their", "there",
    "here", "this", "these", "those", "his", "her", "your", "our", "should", "must", "can",
    "will", "would", "shows", "show", "display", "displays", "opens", "closes", "collapses",
    "appears", "becomes", "lets", "allows", "enables", "provides", "supports",
}


def _clean(phrase: str) -> str:
    """去冠词/标点/尾部角色名词，压空白。返回 "" 表示丢弃。"""
    s = re.sub(r"\s+", " ", (phrase or "").strip(" \t\"'`.,;:!?-–—"))
    s = re.sub(r"^(?:an|a|the)\s+", "", s, flags=re.I)
    # 尾部角色名词去掉（`the search field` → `search`；名字不该带角色词）
    for noun in sorted(ROLE_NOUNS, key=len, reverse=True):
        s = re.sub(rf"\s+\b{re.escape(noun)}\b\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,.")
    if not s or len(s) < 3 or len(s) > MAX_NAME_CHARS:
        return ""
    if len(s.split()) > MAX_NAME_WORDS:
        return ""
    if re.search(r"[\"\u201c\u201d`]", s):   # 引号碎片是**引号式规则**的活，不是散文的
        return ""
    if RE_HAS_DIGIT.search(s):            # `0 results` / `2.3.1` 这类是测试数据
        return ""
    if any(w in BAD_WORDS for w in s.lower().split()):   # 从句碎片，不是名词短语
        return ""
    if s.lower() in STOPWORDS:
        return ""
    return s


def extract_prose_targets(text: str) -> list[tuple[str, str, str]]:
    """从**一段需求文本**抽散文靶子。

    返回 `[(role, name, pattern)]`：
      · `name` 非空 = 有名字的目标（引号式之外的那些）
      · `name` 为空 = **纯 role 目标**（`complementary` 侧栏这类，判据只要 role 存在）
    去重后返回；顺序 = 出现顺序。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(role: str, name: str, pattern: str) -> None:
        key = (role or "", name.lower())
        if key in seen:
            return
        seen.add(key)
        out.append((role or "", name, pattern))

    # ① `the X <角色名词>`（最可靠：角色名词就在旁边）
    for m in RE_ROLE_NOUN_AFTER.finditer(text):
        phrase, noun = m.group(1), m.group(2).lower()
        name = _clean(phrase)
        role = ROLE_NOUNS.get(noun)
        if name:
            add(role or "", name, "prose_role_noun")
        if not name and noun in PURE_ROLE_NOUNS:
            add(PURE_ROLE_NOUNS[noun], "", "prose_pure_role")

    # ② `Click the X link` / `fill in the X field` 这类**动词 + 宾语**
    for m in RE_VERB_OBJ.finditer(text):
        tail = m.group(1)
        # 宾语里若带角色名词，按它定角色
        role = ""
        for noun, r in ROLE_NOUNS.items():
            if re.search(rf"\b{re.escape(noun)}\b", tail, re.I):
                role = r or ""
                break
        name = _clean(tail)
        if name:
            add(role, name, "prose_verb_object")

    # ③ 纯 role 结构（侧栏/导航/编辑器）—— 判据可能只要 role 存在（keep 的 complementary ×7）
    for noun, role in PURE_ROLE_NOUNS.items():
        if re.search(rf"\b{re.escape(noun)}\b", text, re.I):
            add(role, "", "prose_pure_role")

    return out


def interaction_units(tree, a11y) -> tuple[int, int]:
    """(交互单元数, 其中有 a11y 目标的数) —— §4.1 的验收指标用它算"空转率"。

    分母口径（裁定原文）：**场景步骤里含 click/select/enter/check/submit 的单元**；
    纯散文、无点击/输入的单元本来就不该有可访问名。所以分母**收窄**，避免"空转率下降"
    只是分母里混进了本来就无需命名的单元。
    """
    by_req = a11y.by_req()
    units = 0
    with_target = 0
    for n in tree.ordered():
        if not (n.is_leaf and n.scenarios):
            continue
        has_target = bool(by_req.get(n.id))
        for sc in n.scenarios:
            text = " ".join(((s.content or "") + " " + (s.keyword or "")) for s in sc.steps)
            if not re.search(rf"\b{VERB}\b", text, re.I):
                continue
            units += 1
            if has_target:
                with_target += 1
    return units, with_target
