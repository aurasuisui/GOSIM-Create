"""判据侧的「靶子闭包」发动机 —— ②③ 两个工具**共用**（一处实现，别编两遍）。

**为什么要有它**（`PLAN.md` §7 的阶梯 ②③）：闸门是"每个 app 非零"，而 `keep` 那轮 31 条失败
**全是"名字/角色对不上"**。失败不是随机的：**判据要什么名字，是写在测试与 `helpers.ts` 里的**——
所以"这个名字会不会出现"在**判分之前**就能判，而这一步**零 token**。

三步（`PLAN.md` §7 ②）：
  1. **数候选 REQ 的 spec 调用了几个 helper** —— 调用越少越"入门"（`keep` 的 `REQ-2.1` 只用 2 个）；
  2. **取闭包** —— 这些 helper 的**函数体**里（含互相调用）出现的每一个定位器都是该子集的前置；
  3. **对表** —— 闭包里每个定位器：要么需求文本点名了它，要么必须显式进该 app 的生成硬清单。
     **未闭合 = 不许开工。**

⚠️ **覆盖率是必打项**（纪律 15 的原型）：本模块只认一部分写法
（`getByRole` / `getByLabel` / `getByText` / `getByPlaceholder` / `getByTestId` / `locator`）。
**"认了 M 处 / 共 N 处"必须每次都打**——否则低 M 会把"漏抽"伪装成"没有缺失"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---- 定位器调用：先数总数（覆盖率的分母），再数我们认得的（分子）----
RE_ANY_LOCATOR = re.compile(r"\.(?:getBy[A-Za-z]+|locator)\s*\(")
RE_CALL_GETBY = re.compile(r"\.getBy([A-Za-z]+)\s*\(")

_ROLE = r"getByRole\(\s*['\"](\w+)['\"]"
_NAME_REGEX = r"\s*,\s*\{\s*name:\s*/([^/\n]+)/"
_NAME_STR = r"\s*,\s*\{\s*name:\s*['\"]([^'\"]+)['\"]"
RE_ROLE_NAMED_RE = re.compile(_ROLE + _NAME_REGEX)
RE_ROLE_NAMED_STR = re.compile(_ROLE + _NAME_STR)
RE_ROLE_BARE = re.compile(_ROLE + r"\s*\)")
RE_LABEL = re.compile(r"getByLabel\(\s*(?:/([^/\n]+)/|['\"]([^'\"]+)['\"])")
RE_TEXT = re.compile(r"getByText\(\s*(?:/([^/\n]+)/|['\"]([^'\"]+)['\"])")
RE_PLACEHOLDER = re.compile(r"getByPlaceholder\(\s*(?:/([^/\n]+)/|['\"]([^'\"]+)['\"])")
RE_TESTID = re.compile(r"getByTestId\(\s*(?:/([^/\n]+)/|['\"]([^'\"]+)['\"])")
# `getByTitle` —— keep 的判据在用（实测：它是"剩余未识别 8 处"的全部来源）。
# 这条恰好说明覆盖率指标的用途：**残余片段说出漏的是什么**，只看百分比看不出来。
RE_TITLE = re.compile(r"getByTitle\(\s*(?:/([^/\n]+)/|['\"]([^'\"]+)['\"])")
RE_ROLE_HELPER = re.compile(r"\brole:\s*['\"](\w+)['\"]")
RE_NAME_HELPER = re.compile(r"\bname:\s*(?:/([^/\n]+)/|['\"]([^'\"]+)['\"])")
# 裸 `page.getByRole('x')` 之外的形态：`page.locator('...')`
RE_CSS = re.compile(r"\.locator\(\s*['\"]([^'\"]{1,80})['\"]")

# ---- **变量式**定位器（bookstack 那种风格）----
# `page.getByRole('button', { name })` 是**简写属性**：名字由**调用方**传入（参数）。
# 不认这种形态 → 覆盖率会掉到个位数，而"漏抽"会伪装成"没有缺失"（纪律 15 的原型）。
# 实测（2026-09-22）：bookstack 的 helpers **整族都是这种写法**，只认字面量时覆盖率 5%。
# `{ name }` 简写与 `{ name: someIdent }` 是**同一族**（名字由调用方传入）——
# 实测：bookstack 用前者、keep 的 `pattern` 用后者；只认前者会把 keep 的覆盖率压到 93%。
RE_ROLE_VAR = re.compile(_ROLE + r"\s*,\s*\{\s*name\s*(?::\s*(?!\s*[/'\"])[^}]+\s*)?,?\s*\}\s*\)")
RE_VAR_ARG = re.compile(r"getBy(?:Label|Text|Placeholder|TestId|Title)\(\s*(?!\s*[/'\"])[^)]*\)")
# 调用方传进来的字符串（`h.clickNamed(page, 'Shelf 4.1')` 或 fixture 引用）
RE_H_CALL_ARGS = re.compile(r"\bh\.(\w+)\s*\(")
STOP_ARG_WORDS = {"button", "link", "tab", "menuitem", "heading", "page", "text", "true", "false"}

# helper 的导出（body 用花括号配对取，见 helper_bodies）
RE_EXPORT_FN = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.M)
# 🔴 **非导出的函数也要收**（2026-09-22 实测踩到）：stackoverflow 把定位器集中在一个**内部**函数里
# （`resolveNamed`），所有导出 helper 都转发给它 → 只收导出时，那些 spec 的闭包里
# **一条定位器都没有**，覆盖率显示 `0/0`（"本文件没有定位器调用"）。
# **0/0 看起来像"干净"，其实是解析器没看见**——这正是"覆盖率每次必打"的用途（纪律 15 用在它自己身上）。
RE_EXPORT_CONST_FN = re.compile(r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>", re.M)
RE_EXPORT_CONST_OBJ = re.compile(r"^export\s+const\s+(\w+)\s*=\s*\{", re.M)

RE_H_CALL = re.compile(r"\bh\.(\w+)\s*\(")                    # spec 里：h.openShelves(page)
RE_BARE_CALL = re.compile(r"(?<![\w.])(\w+)\s*\(")            # helpers 内部互相调用
RE_FIXTURE_REF = re.compile(r"\bh\.FIXTURES((?:\.\w+)+)")


@dataclass
class Target:
    """一个"判据会去找的东西"。`kind` 说明它是怎么被找的（决定产物侧怎么对表）。"""
    kind: str          # role|label|text|placeholder|testid|css|fixture
    name: str          # 可访问名 / 文本 / 选择器 / fixture 值
    role: str = ""     # kind=role 时才有
    where: str = ""    # 来自哪个文件的哪一段（留痕用）
    # `all`（默认）= 这些名字**都要能命中**（`expectTextsVisible` 那种合取）；
    # `any` = **任一个命中即可**（`expectAnyVisible([a,b,c])` 那种析取）。
    # 🔴 为什么必须分（2026-09-23 实测，ctrip）：把析取当成合取会**凭空要求产物多显示几个词**
    # （假阳性方向），并让硬清单被灌水。识别方式 = 被调用的 helper 名字里含 `any`。
    mode: str = "all"
    # 同一个「要求单元」的 id（`expectAnyVisible([[a,b],[c]])` 里 **外层每个元素 = 一个单元**：
    # 单元内"任一"，单元之间"都要"）。为什么必须分（2026-09-23 实测，ctrip）：那族实参是**嵌套数组**，
    # 扁平化两头都错 —— 既可能把 3 个单元并成 1 个（太松），也可能把单元内的候选当成各自必需（太紧）。
    group: str = ""
    # **族**（PLAN 裁决五）：这个靶子的**消费方 helper** 属于哪一族 → 决定"可命中性"要用哪套位置集合。
    # 命名族 = 8 个 role 的可访问名 ∪ getByText 文本节点；字段族 = label ∪ placeholder ∪ 4 个输入 role。
    # 为什么必须分：**两族的位置集合不同，混用会两个方向都误判**（实测见 `verify/hittable.py` 的表）。
    family: str = ""


@dataclass
class Coverage:
    recognized: int = 0
    total: int = 0

    def add(self, other: "Coverage") -> None:
        self.recognized += other.recognized
        self.total += other.total

    def __str__(self) -> str:
        if not self.total:
            return "0/0（本文件没有定位器调用）"
        pct = 100.0 * self.recognized / self.total
        return f"{self.recognized}/{self.total}（{pct:.0f}%）"


def spec_dir(app_dir: Path) -> Path:
    return Path(app_dir) / "tests"


def helpers_path(app_dir: Path) -> Path:
    return spec_dir(app_dir) / "helpers.ts"


def _strip_strings_and_comments(text: str) -> str:
    """把字符串字面量与注释换成等长空白（花括号配对时用，别数错）。"""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == c:
                    break
                j += 1
            for k in range(i, min(j + 1, n)):
                out[k] = " "
            i = j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def _body_after(text: str, start: int) -> str:
    """从 `start` 往后找第一个 `{`，按配对取到它的 `}`（用去字符串版本定位）。"""
    clean = _strip_strings_and_comments(text)
    i = clean.find("{", start)
    if i < 0:
        return ""
    depth = 0
    for j in range(i, len(clean)):
        if clean[j] == "{":
            depth += 1
        elif clean[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return text[i:]


def helper_bodies(text: str) -> dict[str, str]:
    """`helpers.ts` 里的导出 → 函数体（只取函数；`FIXTURES` 那种对象另算）。"""
    out: dict[str, str] = {}
    for rx in (RE_EXPORT_FN, RE_EXPORT_CONST_FN):
        for m in rx.finditer(text):
            out[m.group(1)] = _body_after(text, m.end())
    return out


def fixtures_object(text: str) -> str:
    for m in RE_EXPORT_CONST_OBJ.finditer(text):
        if m.group(1) == "FIXTURES":
            return _body_after(text, m.end() - 1)
    return ""


def fixture_values(text: str, ref_paths: list[str]) -> list[str]:
    """把 `h.FIXTURES.a.b.c` 这类引用解析成实际字符串值（那些是**要显示在页面上**的数据）。"""
    obj = fixtures_object(text)
    if not obj:
        return []
    out: list[str] = []
    for path in ref_paths:
        keys = [k for k in path.split(".") if k]
        cur = obj
        for k in keys:
            m = re.search(rf"\b{re.escape(k)}\s*:\s*(\{{|['\"])", cur)
            if not m:
                cur = ""
                break
            if m.group(1) == "{":
                cur = _body_after(cur, m.end() - 1)
            else:
                q = cur[m.end() - 1]
                j = cur.find(q, m.end())
                out.append(cur[m.end():j])
                cur = ""
                break
        else:
            # 整段找到底了但没取到值（引用的是对象）→ 把该子树里的字符串都算上
            for s in re.findall(r"['\"]([^'\"]{2,80})['\"]", cur):
                out.append(s)
    return [s for s in dict.fromkeys(out) if s]


RE_LOCATOR_CALL = re.compile(r"\.(getBy[A-Za-z]+|locator)\s*\(")
RE_LEADING_ROLE = re.compile(r"^\s*['\"](\w+)['\"]")


def _is_literal(arg: str) -> bool:
    """实参是不是"字面量"（正则字面量 `/x/` 或引号串 `'x'`）。"""
    a = arg.lstrip()
    return bool(a) and (a[0] == "/" or a[0] in "'\"")


def _literal_value(arg: str) -> str:
    a = arg.lstrip()
    if not a:
        return ""
    if a[0] == "/":                     # 正则字面量 → 取到未转义的结束斜杠
        j = 1
        while j < len(a):
            if a[j] == "\\":
                j += 2
                continue
            if a[j] == "/":
                break
            j += 1
        return a[1:j].strip().strip("^$").strip()
    q = a[0]
    j = a.find(q, 1)
    return a[1:j].strip() if j > 0 else ""


def locators(text: str, where: str = "") -> tuple[list[Target], Coverage]:
    """抓出一个文件里的定位器靶子 + 覆盖率（认了 M / 共 N）。

    **实现方式**（2026-09-22 重写）：**逐个"调用点"分类**，不用"正则套正则再算 span"。
    旧写法在 `` `^${escapeRegExp(label)}$` `` 这类**模板串里带 `}`** 的实参上会静默漏
    （span 隶属判定失败）→ 覆盖率虚低、且残余片段说不清。现在用**字符串感知的实参扫描**
    （`_args_of`），对每个调用点只问一句：**它的名字实参是字面量还是表达式？**
      · 字面量 → 记成具体靶子（`role/button 'New Shelf'`）
      · 表达式（含 `{ name }` 简写、`new RegExp(...)`、`patterns[0]`）→ `xxx(var)`：
        **名字由调用方传入**，真正的值在 `call_arg_targets()` 里从调用点取。
      · 两者都不是（例如 `getByRole(roleVar, …)`——**连 role 都是变量**）→ **未识别**，进不了靶子，
        但**必须计入覆盖率分母**。
    """
    got: list[Target] = []
    total = 0
    recognized = 0

    for m in RE_LOCATOR_CALL.finditer(text):
        fn = m.group(1)
        total += 1
        arg_span = _args_of(text, m.end() - 1)
        if arg_span is None:
            continue
        if fn == "locator":
            v = _literal_value(arg_span)
            if v:
                got.append(Target("css", v, "", where))
                recognized += 1
            continue

        if fn == "getByRole":
            mrole = RE_LEADING_ROLE.match(arg_span)
            if not mrole:
                continue                       # role 都是变量 → 未识别
            role = mrole.group(1)
            rest = arg_span[mrole.end():]
            brace = _braces_of(rest, rest.find("{")) if "{" in rest else None
            if brace is None:                  # 裸 role（不要名字）
                got.append(Target("role", "", role, where))
                recognized += 1
                continue
            nm = re.search(r"\bname\s*:\s*", brace)
            if nm:
                expr = brace[nm.end():].strip()
                if _is_literal(expr):
                    got.append(Target("role", _literal_value(expr), role, where))
                else:
                    got.append(Target("role(var)", "", role, where))
                recognized += 1
            elif re.search(r"\bname\s*,?\s*\}\s*$", brace):   # `{ name }` 简写
                # ⚠️ `_braces_of` 返回的**含花括号**（`'{ name }'`），所以这里必须匹配到 `}`——
                # 只写 `name\s*$` 会一条都匹配不上（实测踩过：bookstack 的整族 helper 因此掉到 61%）
                got.append(Target("role(var)", "", role, where))
                recognized += 1
            continue

        # getByLabel / getByText / getByTitle / getByPlaceholder / getByTestId
        kind = fn[len("getBy"):].lower()
        if _is_literal(arg_span):
            got.append(Target(kind, _literal_value(arg_span), "", where))
            recognized += 1
        elif arg_span.strip():
            got.append(Target(kind + "(var)", "", "", where))
            recognized += 1

    # 附加：`role:` + `name:` 组合（`expect(locator).toHaveRole` / toPattern 一类）
    for m in RE_ROLE_HELPER.finditer(text):
        nm = RE_NAME_HELPER.search(text[m.end():m.end() + 200])
        got.append(Target("role", (nm.group(1) or nm.group(2)).strip() if nm else "",
                          m.group(1), where + "（role/name 组合）"))
    return _dedup(got), Coverage(recognized, total)


def unrecognized_snippets(text: str, limit: int = 0) -> list[str]:
    """**未被解析器识别**的定位器调用原文（给工具打印用）。

    为什么必须打印它：覆盖率只说"漏了多少"，**snippet 才说出"漏的是什么"**——
    评审要求"覆盖率在第一次用它挡判分之前必须被人看过一眼"，看的就是这个。
    """
    out: list[str] = []
    for m in RE_LOCATOR_CALL.finditer(text):
        arg = _args_of(text, m.end() - 1) or ""
        fn = m.group(1)
        ok = False
        if fn == "locator":
            ok = bool(_literal_value(arg))
        elif fn == "getByRole":
            ok = bool(RE_LEADING_ROLE.match(arg))
        else:
            ok = _is_literal(arg) or bool(arg.strip())
        if not ok:
            out.append(" ".join(text[m.start():m.start() + 72].split()))
    return out[:limit] if limit else out


def _dedup(items: list[Target]) -> list[Target]:
    seen: dict[tuple[str, str, str], Target] = {}
    for t in items:
        key = (t.kind, t.role, t.name)
        if key not in seen:
            seen[key] = t
        else:                                   # 合并来源，便于留痕
            if t.where and t.where not in seen[key].where:
                seen[key].where += ", " + t.where
    return list(seen.values())


def spec_files(app_dir: Path, req_ids: list[str] | None = None) -> list[Path]:
    """子集对应的 spec 文件；不给子集就返回全部。"""
    d = spec_dir(app_dir)
    out = sorted(p for p in d.glob("*.spec.ts"))
    if not req_ids:
        return out
    picked = []
    for p in out:
        text = p.read_text(encoding="utf-8", errors="replace")
        for rid in req_ids:
            # spec 文件名或正文里出现该 REQ（正文里通常是 `test('REQ-4.1: …')`）
            if rid in p.name or re.search(rf"\b{re.escape(rid)}\b", text):
                picked.append(p)
                break
    return picked


# helper 名的**动词分类** —— 决定它实参里那些字符串是"必须显示"还是"测试自己输入"。
# 为什么必须分（实测踩过）：`h.createNote(page, 'Weekend plan', 'Visit the farmers market…')`
# 里的值由**测试自己敲进表单**，产物**不需要预先包含它们**；
# 而 `h.expectTextsVisible(page, [/BookStack/i])` 里的值**必须**出现在页面上。
# 把两者混为一谈 → 硬清单会被"测试输入"灌水（又一次"又宽又松"）。
NEED_VERBS = ("expect", "assert", "see", "visible", "should", "verify", "check", "wait", "title")
INPUT_VERBS = ("fill", "create", "type", "enter", "input", "set", "write", "edit", "update",
               "search", "add", "delete", "remove", "archive", "restore", "move", "toggle",
               "click", "open", "select", "submit", "upload", "choose", "hover", "drag",
               # 认证类 helper 也是"把值敲进表单"（实测：它的 fixture 是账号/口令，
               # 属**测试自己输入**，产物不必预先包含）——不归进来的话会落进"无法分类"桶
               "login", "log_in", "signin", "signup", "register", "logout")


def classify_helper(name: str) -> str:
    """`need`（本应显示）/ `input`（测试自己输入）/ `unknown`（不认识 → 人工看一眼）。

    ⚠️ **这是"记不住形参角色时的兜底"**（PLAN 裁决五的必修项）：它**只看 helper 名字里的动词**，
    而实测 `clickNamed` / `openBooks` / `fillField` 的实参**全是"必须预先存在"的名字** ——
    按名字判会把它们整类打成 `input`，于是 bookstack **32/34 条失败的首阻一条都没进硬清单**。
    → 真实判据是 `param_roles()`（**跟着形参语义走**）；本函数只在 helper 体解析不到时兜底。
    """
    low = name.lower()
    if any(v in low for v in NEED_VERBS):
        return "need"
    if any(v in low for v in INPUT_VERBS):
        return "input"
    return "unknown"


# ---- 形参语义（PLAN 裁决五：分类跟着**形参**走，不跟着 helper 名里的动词走）----
RE_FN_PARAMS = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", re.M)
RE_ARROW_PARAMS = re.compile(
    r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>", re.M)
RE_LOCATOR_SINK = re.compile(r"\.(?:getBy[A-Za-z]+|locator)\s*\(")
# **值**的落点：这些调用的实参是"测试自己敲进去的内容"，不是要找的名字。
VALUE_SINKS = (".fill(", ".type(", ".selectOption(")


def _param_names(sig: str) -> list[str]:
    out: list[str] = []
    for part in split_top_level(sig):
        p = re.split(r"[:=]", part.strip(), 1)[0].strip().lstrip(".")
        if p:
            out.append(p)
    return out


def _call_snippets(text: str, rx: re.Pattern) -> list[str]:
    """把 `rx` 匹配到的每次调用连同它的实参括号**配对取全**（用于看"谁出现在里面"）。"""
    out: list[str] = []
    for m in rx.finditer(text):
        i = text.find("(", m.end() - 1)
        if i < 0:
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    out.append(text[i:j + 1])
                    break
    return out


_FIELD_ROLES = {"textbox", "searchbox", "combobox", "spinbutton"}


@lru_cache(maxsize=8)
def helper_families(helpers_text: str) -> dict[str, str]:
    """每个 helper 消费的是哪一族的位置集合（`named` / `field` / `named+field`）。

    🔴 **由"真正消费它的那条链"决定，不硬编码 helper 名表**（PLAN 裁决五）：
    实测过 `fillPasswordLogin` 走**字段族**、而同一个文件里的 `expectPasswordLoginForm` 走**命名族** ——
    两族的消费方不同，**不能混读**。判法 = 在 helpers.ts 内走调用图：body 里出现的
    `getByRole(<role>)` / `getByText` / `getByLabel` / `getByPlaceholder` 直接给族，
    调用了别的 helper 则继承它的族（迭代到不动点）。
    """
    funcs: dict[str, str] = {}
    for rx in (RE_FN_PARAMS, RE_ARROW_PARAMS):
        for m in rx.finditer(helpers_text):
            funcs[m.group(1)] = _body_after(helpers_text, m.end())
    fams: dict[str, set[str]] = {}
    for name, body in funcs.items():
        clean = _strip_strings_and_comments(body)
        got: set[str] = set()
        for m in re.finditer(r"\.getByRole\(\s*['\"](\w+)['\"]", clean):
            got.add("field" if m.group(1) in _FIELD_ROLES else "named")
        if re.search(r"\.getByText\s*\(", clean):
            got.add("named")
        if re.search(r"\.getBy(Label|Placeholder)\s*\(", clean):
            got.add("field")
        fams[name] = got
    for _ in range(3):                       # 调用图传播
        changed = False
        for name, body in funcs.items():
            for callee, _args in _inner_calls(_strip_strings_and_comments(body), funcs):
                if fams.get(callee) and not fams[callee] <= fams[name]:
                    fams[name] |= fams[callee]
                    changed = True
        if not changed:
            break
    return {k: "+".join(sorted(v)) for k, v in fams.items()}


@lru_cache(maxsize=8)
def param_roles(helpers_text: str) -> dict[str, list[str]]:
    """每个 helper 的**每个形参**的角色：`need` / `input` / `unknown`。

    判据（`PLAN.md` 裁决五，2026-09-23 拍板）：
    **`fillField` 的第 2 参（形参名就叫 `labelOrPlaceholder`）与 `click*` 的全部实参 =
    "必须预先存在"**；只有**填入值**才是"测试自己输入"。

    怎么做到的（**不硬编码 helper 名表**，各 app 的 helper 名不同）：
    1. 取每个函数的形参与函数体；
    2. **污点传播**：形参 → 由它派生的局部名（`const name = toPattern(value)` 里的 `name` 也算）；
    3. **直接证据**：某形参（或其派生名）出现在 `getBy*/.locator(` 里 → `need`；
       出现在 `.fill(/.type(/.selectOption(` 里 → `input`；
    4. **调用图传播**：形参被传给另一个 helper 的第 k 个形参 → 继承那个形参的角色。
    """
    funcs: dict[str, tuple[str, str]] = {}
    for rx in (RE_FN_PARAMS, RE_ARROW_PARAMS):
        for m in rx.finditer(helpers_text):
            funcs[m.group(1)] = (m.group(2), _body_after(helpers_text, m.end()))
    roles = {n: ["unknown"] * len(_param_names(sig)) for n, (sig, _) in funcs.items()}
    taints: dict[str, dict[str, set[str]]] = {}

    for name, (sig, body) in funcs.items():
        params = _param_names(sig)
        clean = _strip_strings_and_comments(body)
        t = {p: {p} for p in params}
        # 污点传播跑**两遍**：`for (const pattern of patterns)` 这类**循环变量**要先被认出来，
        # 体内 `const name = toPattern(pattern)` 才接得上（实测踩过：一遍时 `fillField` 的
        # `labelOrPlaceholder` 只传到 `patterns` 就断了，角色停在 unknown）。
        for _ in range(2):
            for line in clean.splitlines():        # 形参 → 派生局部名
                m = re.match(r"\s*(?:const|let|var)\s+(\w+)\s*=\s*(.+)$", line)
                if not m:
                    m = re.match(r"\s*for\s*\(\s*(?:const|let|var)\s+(\w+)\s+of\s+(.+?)\s*\)", line)
                if not m:
                    continue
                lhs, rhs = m.group(1), m.group(2)
                for p, names in t.items():
                    if any(re.search(rf"\b{re.escape(n)}\b", rhs) for n in names):
                        names.add(lhs)
        taints[name] = t
        locator_args = _call_snippets(clean, RE_LOCATOR_SINK)
        value_args = [s for sink in VALUE_SINKS
                      for s in _call_snippets(clean, re.compile(re.escape(sink)))]
        for p, names in t.items():
            hit = lambda chunks: any(          # noqa: E731
                re.search(rf"\b{re.escape(n)}\b", c) for c in chunks for n in names)
            if hit(locator_args):
                roles[name][params.index(p)] = "need"
            elif hit(value_args):
                roles[name][params.index(p)] = "input"

    # 调用图传播（迭代到不动点；helper 调用图很浅，3 遍足够）
    for _ in range(3):
        changed = False
        for name, (_, body) in funcs.items():
            clean = _strip_strings_and_comments(body)
            for callee, args_str in _inner_calls(clean, funcs):
                for k, arg in enumerate(split_top_level(args_str), start=0):
                    if k >= len(roles.get(callee, [])):
                        continue
                    new = roles[callee][k]
                    if new not in ("need", "input"):
                        continue
                    for p, names in taints.get(name, {}).items():
                        if any(re.search(rf"\b{re.escape(n)}\b", arg) for n in names):
                            idx = _param_names(funcs[name][0]).index(p)
                            if roles[name][idx] != new:
                                roles[name][idx] = new
                                changed = True
        if not changed:
            break
    return roles


def _inner_calls(clean_body: str, funcs: dict) -> list[tuple[str, str]]:
    """body 里对本文件其它函数的调用 → `(callee, 实参串)`。"""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"(?<![\w.])(\w+)\s*\(", clean_body):
        if m.group(1) not in funcs:
            continue
        i = m.end() - 1
        depth = 0
        for j in range(i, len(clean_body)):
            if clean_body[j] == "(":
                depth += 1
            elif clean_body[j] == ")":
                depth -= 1
                if depth == 0:
                    out.append((m.group(1), clean_body[i + 1:j]))
                    break
    return out


def call_arg_targets(spec_text: str, helpers_text: str, where: str = "") -> list[Target]:
    """调用方传进 helper 的**字符串**（bookstack 风格：名字由调用方给）。

    为什么必须有：那族 helper 的定位器是 `{ name }` 变量式 —— 名字不写在 helper 里，
    而在**spec 的调用实参**里（`h.clickNamed(page, h.FIXTURES.shelves.details.name)`）。
    不取这一层，闭包就只剩"这一族存在"，而没有**具体要找什么**。

    ⚠️ 每个靶子都带**动词分类**（见 `classify_helper`）——`需要显示` 与 `测试自己输入`
    是两种不同的东西，混在一起会让硬清单灌水。
    """
    out: list[Target] = []
    calls = [(m.group(1), m.end() - 1) for m in RE_H_CALL_ARGS.finditer(spec_text)]
    return _arg_targets(spec_text, calls, helpers_text, where, prefix="h.")


def bare_call_arg_targets(body: str, names: set[str], helpers_text: str, where: str = "") -> list[Target]:
    """**helper 体内的**调用实参（ctrip 风格：regex 字面量写在 helper 内部）。

    为什么必须有（2026-09-23 实测，ctrip）：它的 spec 只写 `h.ensurePasswordLogin(page)`（**无参数**），
    而真正的靶子写在 helper 体内 —— `clickIfVisible(page, [/账号登录/, /password login/i])`。
    只扫 spec 的实参 ⇒ 闭包里"具体靶子 0 个"（**看起来像"干净"，其实是没看见** —— 纪律 15 的老形态）。
    """
    calls = [(m.group(1), m.end() - 1) for m in RE_BARE_CALL.finditer(body) if m.group(1) in names]
    return _arg_targets(body, calls, helpers_text, where, prefix="")


def _arg_targets(text: str, calls, helpers_text: str, where: str, *, prefix: str) -> list[Target]:
    """从给定调用点里抽"实参靶子"（spec 与 helper 体共用这一段）。"""
    out: list[Target] = []
    roles_all = param_roles(helpers_text)
    fams_all = helper_families(helpers_text)
    for hname, open_paren in calls:
        fallback = classify_helper(hname)          # 只在**形参语义**拿不到时兜底
        role_list = roles_all.get(hname) or []
        fam = fams_all.get(hname, "")             # 族：决定"可命中性"用哪套位置集合
        mode = "any" if "any" in hname.lower() else "all"   # `expectAnyVisible` 是析取
        args = _args_of(text, open_paren)
        if not args:
            continue
        where_h = f"{where or 'spec'} → {prefix}{hname}()"
        # **要求单元的解析规则**（2026-09-23 按 ctrip / bookstack 的真实形态定了三轮）：
        #   第 1 个实参是 scope（page）；**第 2 个起**：
        #   · `need`（名字/文本**必须预先存在**）→ 实参若为数组，**每个元素 = 一个要求单元**（都要满足）；
        #     单元内部若还是数组 → 里面是**候选**（任一即可）。
        #   · `input`（**测试自己输入的值**）→ 整串实参的候选**合成一个单元**（**任一即可**）：
        #     那族 helper 的实现是"挨个试，哪个可见就用哪个"（实测：`clickIfVisible`），
        #     把候选当成"每个都要"会凭空要求产物多显示几个词（假阳性方向）。
        #   对照：
        #     `expectTextsVisible(page, [/a/, /b/])`     → 2 单元、各 1 候选 → a 与 b **都要**
        #     `expectAnyVisible(page, [[/a/,/b/], [/c/]])` → 2 单元，单元内任一 → (a 或 b) **且** c
        #     `clickIfVisible(page, [/x/, /y/])`         → 1 单元，候选 x/y → **任一即可**
        #
        # 🔴 **角色按"第几个实参"判，不按 helper 名判**（PLAN 裁决五的必修项，2026-09-23）：
        #   实测 `classify_helper('clickNamed')='input'`、`('openBooks')='input'`、`('fillField')='input'`
        #   → bookstack **32/34 条失败的首阻**（`Books` 15 条 / `Shelves` 11 条 / `Email address` 6 条）
        #   一条都没进硬清单（它们躺在"🟡 产物不必预先包含"那一栏里）。
        #   真实判据：`fillField` 的第 2 参（`labelOrPlaceholder`）与 `click*` 的全部实参 =
        #   **必须预先存在**；只有**填入值**才是"测试自己输入"（由 `param_roles()` 从函数体里推）。
        value_args = split_top_level(args)[1:]        # 跳过 scope
        input_items: list[str] = []                    # 归"测试自己输入"的实参 → 合成一个 any 单元
        for arg_idx, arg in enumerate(value_args, start=1):
            # 形参语义优先；**unknown 时退回名字判**（保守：不把"不知道"当成"输入" ——
            # 旧行为里 unknown 会落到"测试自己输入"那一栏，等于**默认漏一条**）
            role = (role_list[arg_idx]
                    if arg_idx < len(role_list) and role_list[arg_idx] in ("need", "input")
                    else fallback)
            if role != "need":
                input_items.append(arg)
                continue
            cls = "need"
            stripped = arg.strip()
            units = (split_top_level(stripped[1:-1])
                     if stripped.startswith("[") and stripped.endswith("]")
                     else [stripped])
            for unit_idx, unit in enumerate(units):
                gid = f"{where_h}#{arg_idx}.{unit_idx}"
                u = unit.strip()
                cand = (split_top_level(u[1:-1])
                        if u.startswith("[") and u.endswith("]") else [u])
                elem_mode = "any" if len(cand) > 1 else "all"
                for item in cand:
                    lits = [l for l in re.findall(r"['\"]([^'\"]{2,80})['\"]", item)
                            if l.lower() not in STOP_ARG_WORDS]
                    pats = [p.strip().strip("^$").strip()
                            for p in re.findall(r"/([^/\n]{2,80})/[gimsuy]*", item)]
                    pats = [p for p in pats if p and p.lower() not in STOP_ARG_WORDS]
                    for lit in lits:
                        out.append(Target(f"arg:{cls}", lit, "", where_h, elem_mode, gid, fam))
                    for pat in pats:
                        out.append(Target(f"arg(re):{cls}", pat, "", where_h, elem_mode, gid, fam))
                    refs = [r.group(1) for r in RE_FIXTURE_REF.finditer(item)]
                    vals = fixture_values(helpers_text, refs)
                    for v in vals:
                        out.append(Target(f"fixture:{cls}", v, "", where_h, elem_mode, gid, fam))
                    if refs and not vals:      # 有 fixture 引用却没解析出值 → 记下来，别静默丢
                        for r in refs:
                            out.append(Target(f"fixture?:{cls}", "FIXTURES" + r, "",
                                              f"{where_h}（未解析出值）", elem_mode, gid, fam))
        # 归"测试自己输入"的那些实参：**合成一个 any 单元**（旧语义原样保留 —— 它们是
        # "挨个试"的一组；拆成"每个都要"会把候选误当合取，往假阳性方向走）。
        if input_items:
            cls = "input"
            gid = f"{where_h}#in"
            blob = ",".join(input_items)
            for lit in re.findall(r"['\"]([^'\"]{2,80})['\"]", blob):
                if lit.lower() not in STOP_ARG_WORDS:
                    out.append(Target(f"arg:{cls}", lit, "", where_h, "any", gid, fam))
            for pat in re.findall(r"/([^/\n]{2,80})/[gimsuy]*", blob):
                cleaned = pat.strip().strip("^$").strip()
                if cleaned and cleaned.lower() not in STOP_ARG_WORDS:
                    out.append(Target(f"arg(re):{cls}", cleaned, "", where_h, "any", gid, fam))
            refs = [r.group(1) for r in RE_FIXTURE_REF.finditer(blob)]
            vals = fixture_values(helpers_text, refs)
            for v in vals:
                out.append(Target(f"fixture:{cls}", v, "", where_h, "any", gid, fam))
            if refs and not vals:
                for r in refs:
                    out.append(Target(f"fixture?:{cls}", "FIXTURES" + r, "",
                                      f"{where_h}（未解析出值）", "any", gid, fam))
    return out


def split_top_level(arg_text: str) -> list[str]:
    """按**顶层**逗号切分（括号/方括号/花括号内的逗号不算）。

    用途：`expectAnyVisible(page, [[a, b], [c]])` 的**外层元素**要各自成一个"要求单元"。
    用去字符串版本做深度计数、再按原串切 —— 别用 `str.split(",")`（会把嵌套数组切碎）。
    """
    clean = _strip_strings_and_comments(arg_text)
    depth = 0
    out: list[str] = []
    start = 0
    for i, ch in enumerate(clean):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(arg_text[start:i])
            start = i + 1
    out.append(arg_text[start:])
    return [x for x in (s.strip() for s in out) if x]


def _braces_of(text: str, idx: int) -> str | None:
    """取一对 `{}` 之间的原文（字符串感知；模板串里的 `${…}` 已被抹掉，不会数错花括号）。"""
    if idx is None or idx < 0:
        return None
    clean = _strip_strings_and_comments(text)
    if idx >= len(clean) or clean[idx] != "{":
        return None
    depth = 0
    for i in range(idx, len(clean)):
        if clean[i] == "{":
            depth += 1
        elif clean[i] == "}":
            depth -= 1
            if depth == 0:
                return text[idx:i + 1]
    return None


def _args_of(text: str, open_paren: int) -> str:
    """取一对括号之间的原文（忽略字符串里的括号）。"""
    clean = _strip_strings_and_comments(text)
    if open_paren >= len(clean) or clean[open_paren] != "(":
        return ""
    depth = 0
    for i in range(open_paren, len(clean)):
        if clean[i] == "(":
            depth += 1
        elif clean[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
    return text[open_paren + 1:]


def closure_for_spec(spec: Path, helpers_text: str, bodies: dict[str, str]) -> tuple[list[Target], Coverage, list[str]]:
    """一个 spec 的闭包：本文件的定位器 + 它调用的 helper（含互相调用）里的定位器。

    返回 (靶子, 覆盖率, 用到的 helper 名)。
    """
    spec_text = spec.read_text(encoding="utf-8", errors="replace")
    targets, cov = locators(spec_text, spec.name)

    used: list[str] = []
    queue = [m.group(1) for m in RE_H_CALL.finditer(spec_text)]
    seen_helpers: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in seen_helpers:
            continue
        seen_helpers.add(name)
        body = bodies.get(name)
        if body is None:
            continue                            # FIXTURES 之类不是函数
        used.append(name)
        sub, sub_cov = locators(body, f"helpers.{name}()")
        targets += sub
        cov.add(sub_cov)
        # helper 体内的**调用实参**（ctrip 那种：regex 字面量写在 helper 里，spec 无参数）
        targets += bare_call_arg_targets(body, set(bodies), helpers_text, f"helpers.{name}()")
        for m in RE_BARE_CALL.finditer(body):    # helper 之间互相调用
            if m.group(1) in bodies:
                queue.append(m.group(1))

    # fixture 值（要显示在页面上的数据）
    refs = [m.group(1) for m in RE_FIXTURE_REF.finditer(spec_text)]
    for v in fixture_values(helpers_text, refs):
        targets.append(Target("fixture", v, "", f"{spec.name} 的 FIXTURES"))
    # **调用方传进 helper 的字符串**（变量式定位器那一族的名字来源）
    targets += call_arg_targets(spec_text, helpers_text, spec.name)
    return _dedup(targets), cov, sorted(set(used))
