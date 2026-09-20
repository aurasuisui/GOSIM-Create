#!/usr/bin/env python3
"""可访问名召回抽检（六个 app）—— 复刻 eval/check_reqcompile.py 检查 3 的口径，
但把「测试用名」的抽取从 12306 专属正则换成**通用规则**。

口径（与检查 3 逐字一致）：
  - 子串匹配、大小写不敏感（Playwright 的 getByRole({name})/getByLabel 默认语义）
  - 覆盖 = 命中数 / 测试用名数

「测试用名」的通用抽取，三步：
  1. 解析 helpers.ts，自动识别「哪个 helper 的哪个参数是名字位置」
     判据：该参数被 toPattern(p) / resolveNamed(scope,p) / resolveField(scope,p) 使用，
     或经 `for (const v of p)` 迭代后 v 命中，或传给已知名字位置的 helper 参数（不动点迭代）
  2. 在 spec 文件里抓这些 helper 调用、名字位置实参的字符串字面量（含数组元素）
  3. 加上 spec / helpers 里直接的 getBy*('...') / getByRole(..., {name: '...'}) 字面量

这是**测量脚本**，不属于提交物；放在工作区外的临时目录。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(r"C:\Users\aurasui\Desktop\Anything\GOSIM Create")
sys.path.insert(0, str(ROOT / "pipeline"))

from reqcompile import extract_accessible_names, load_requirement_tree  # noqa: E402

BENCH = ROOT / "repos" / "arc-bench" / "arc-bench" / "webapp"
APPS = ["12306", "bookstack", "ctrip", "keep", "prestashop", "stackoverflow"]

# 「交互单元」判据（PLAN.md §4.1 裁定 + 第五轮审核 §2.3-2）：
# 场景名字或任一步骤里出现下列动词的单元，才**本来就应该**有可访问名。
# 纯散文、无点击/输入的单元不该进分母——混进来会让「空转率下降」变成假信号。
#
# ⚠️ 必须吃词形变化：需求文本里大量写 `clicks the "Save" button`（第三人称单数），
# 用 `\bclick\b` 会整批漏掉——这与 §三 记过的 `\btab\bs?` 是同一个坑（复数/后缀）。
INTERACTION_VERBS = ("click", "select", "enter", "check", "submit")
INTERACTION_RE = re.compile(
    r"\b(?:" + "|".join(INTERACTION_VERBS) + r")(?:s|es|ed|d|ing|ted|ting)?\b", re.I)


def is_interaction_unit(node) -> bool:
    """该生成单元是否含交互动词（看场景名与全部步骤内容）。"""
    for sc in node.scenarios:
        if sc.name and INTERACTION_RE.search(sc.name):
            return True
        for st in sc.steps:
            if st.content and INTERACTION_RE.search(st.content):
                return True
    return False


# ---------------------------------------------------------------- 实参切分

def split_args(src: str, open_paren: int) -> tuple[list[str], int]:
    """src[open_paren] == '('。返回 (顶层实参源码列表, 右括号之后的下标)。"""
    depth = 0
    args: list[str] = []
    cur: list[str] = []
    i = open_paren
    quote: str | None = None
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                cur.append(src[i:i + 2]); i += 2; continue
            if c == quote:
                quote = None
            cur.append(c)
        elif c in "'\"`":
            quote = c; cur.append(c)
        elif c in "([{":
            depth += 1; cur.append(c)
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(cur))
                return args, i + 1
            cur.append(c)
        elif c == "," and depth == 1:
            args.append("".join(cur)); cur = []
        else:
            cur.append(c)
        i += 1
    return args, i  # 未闭合：调用方自行丢弃


STR_LIT = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"|`([^`$]*)`")
REGEX_LIT = re.compile(r"(?<![\w)\]$])/((?:[^/\\\n]|\\.)+)/[gimsuy]*")


def normalize_regex(body: str) -> str:
    """把正则字面量近似还原成它代表的那个名字（用于子串口径比对）。

    `/^Book$/i` → `Book`；`/sign\\s+in/` → `sign in`；`/questions/i` → `questions`
    """
    s = body
    s = re.sub(r"\\s[+*?]?", " ", s)
    s = s.replace(r"\b", "").replace("^", "").replace("$", "")
    s = s.replace(r"\/", "/")
    s = s.replace("(?:", "").replace("(?i)", "")
    s = s.replace("(", "").replace(")", "")
    s = s.replace(".*", "").replace(".+", "")
    s = s.replace(r"\d", "").replace(r"\w", "").replace(r"\W", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .*|/\\")


def literals_in(arg_src: str) -> tuple[set[str], set[str]]:
    """从实参源码里抽字符串字面量与正则字面量（含数组元素）。

    返回 (引号名集合, 正则名集合)。跳过含 ${} 的模板串。
    """
    quoted: set[str] = set()
    for m in STR_LIT.finditer(arg_src):
        s = decode_escapes(next(g for g in m.groups() if g is not None)).strip()
        if s:
            quoted.add(s)
    rx: set[str] = set()
    for m in REGEX_LIT.finditer(arg_src):
        s = normalize_regex(m.group(1))
        if s and not s.isdigit():
            rx.add(s)
    return quoted, rx


# ------------------------------------------------- 1. 识别「名字位置」参数

FN_START = re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(")
ARROW_START = re.compile(r"(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(")

DIRECT_RULES = (
    r"toPattern\(\s*{p}\b",
    r"resolveNamed\(\s*[\w.\[\]]+\s*,\s*{p}\b",
    r"resolveField\(\s*[\w.\[\]]+\s*,\s*{p}\b",
)


def parse_helpers(src: str) -> dict[str, tuple[list[str], str]]:
    """helper 名 → (形参名列表, 函数体源码)。"""
    starts: list[tuple[int, str, int]] = []
    for rx in (FN_START, ARROW_START):
        for m in rx.finditer(src):
            starts.append((m.start(), m.group(1), m.end() - 1))
    starts.sort()
    out: dict[str, tuple[list[str], str]] = {}
    for i, (start, name, paren) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
        body = src[start:end]
        # 形参表：从 '(' 起找配对 ')' —— paren 是 src 的绝对下标，转成 body 内相对下标
        rel = paren - start
        depth, j, quote = 0, rel, None
        while j < len(body):
            c = body[j]
            if quote:
                if c == "\\": j += 2; continue
                if c == quote: quote = None
            elif c in "'\"`": quote = c
            elif c == "(": depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0: break
            j += 1
        params = []
        for raw in body[rel + 1:j].split(","):
            m = re.match(r"\s*(?:readonly\s+)?(\w+)", raw)
            if m:
                params.append(m.group(1))
        out[name] = (params, body)
    return out


def name_positions(funcs: dict[str, tuple[list[str], str]]) -> dict[str, set[int]]:
    """helper 名 → 名字位置的形参下标集合（不动点）。"""
    hits: dict[str, set[int]] = {}

    def direct(fname: str, body: str, p: str) -> bool:
        if any(re.search(r.format(p=re.escape(p)), body) for r in DIRECT_RULES):
            return True
        # for (const v of p) { ... v 在名字位置 ... }
        for m in re.finditer(r"for\s*\(\s*(?:const|let)\s+(\w+)\s+of\s+" + re.escape(p) + r"\b", body):
            v = m.group(1)
            if any(re.search(r.format(p=re.escape(v)), body) for r in DIRECT_RULES):
                return True
        return False

    for _ in range(6):  # 不动点
        changed = False
        for fname, (params, body) in funcs.items():
            idxs = hits.setdefault(fname, set())
            for i, p in enumerate(params):
                if i in idxs:
                    continue
                ok = direct(fname, body, p)
                if not ok:
                    # 传给已知名字位置的 helper 参数：H(scope, p, ...) 且 H 的该位置已知
                    for callee, cidx in hits.items():
                        for ci in cidx:
                            # 形参下标 ci 在实参里的位置：允许开头多一个 scope 实参 → ci+1
                            for pos in (ci, ci + 1):
                                pat = (r"\b" + re.escape(callee) + r"\s*\("
                                       + r"\s*[\w.\[\]()]+?\s*,\s*" * pos
                                       + r"\s*" + re.escape(p) + r"\s*[,)]")
                                if re.search(pat, body):
                                    ok = True
                if ok:
                    idxs.add(i); changed = True
        if not changed:
            break
    return {k: v for k, v in hits.items() if v}


# ------------------------------------------------------- 2/3. 测试侧用名

GETBY_PATTERNS = (
    r"getByLabel\(\s*'([^']+)'",
    r'getByLabel\(\s*"([^"]+)"',
    r"getByPlaceholder\(\s*'([^']+)'",
    r'getByPlaceholder\(\s*"([^"]+)"',
    r"getByText\(\s*'([^']+)'",
    r'getByText\(\s*"([^"]+)"',
    r"getByTitle\(\s*'([^']+)'",
    r'getByTitle\(\s*"([^"]+)"',
    r"getByRole\(\s*'[a-z]+'\s*,\s*\{[^}]*?name:\s*'([^']+)'",
    r'getByRole\(\s*\'[a-z]+\'\s*,\s*\{[^}]*?name:\s*"([^"]+)"',
    r"hasText:\s*'([^']+)'",
    r'hasText:\s*"([^"]+)"',
)

# 正则形态的定位参数：getByRole(..., { name: /x/i })、getByLabel(/x/i)、hasText: /x/
GETBY_REGEX_PATTERNS = (
    r"getByLabel\(\s*/((?:[^/\\\n]|\\.)+)/[gimsuy]*",
    r"getByPlaceholder\(\s*/((?:[^/\\\n]|\\.)+)/[gimsuy]*",
    r"getByText\(\s*/((?:[^/\\\n]|\\.)+)/[gimsuy]*",
    r"getByTitle\(\s*/((?:[^/\\\n]|\\.)+)/[gimsuy]*",
    r"getByRole\(\s*'[a-z]+'\s*,\s*\{[^}]*?name:\s*/((?:[^/\\\n]|\\.)+)/[gimsuy]*",
    r"hasText:\s*/((?:[^/\\\n]|\\.)+)/[gimsuy]*",
)


def collect_used(app_dir: pathlib.Path) -> tuple[set[str], set[str], dict[str, object]]:
    """返回 (全部测试用名, 仅引号名, 元数据)。两者都按小写去重。"""
    helpers_path = app_dir / "tests" / "helpers.ts"
    src_helpers = helpers_path.read_text(encoding="utf-8")
    funcs = parse_helpers(src_helpers)
    npos = name_positions(funcs)

    specs = sorted((app_dir / "tests").glob("*.spec.ts"))
    texts = {f: f.read_text(encoding="utf-8") for f in specs}
    texts[helpers_path] = src_helpers

    quoted: set[str] = set()
    rx: set[str] = set()

    for f, t in texts.items():
        for p in GETBY_PATTERNS:
            for m in re.finditer(p, t):
                quoted.add(decode_escapes(m.group(1)).strip())
        for p in GETBY_REGEX_PATTERNS:
            for m in re.finditer(p, t, re.S):
                s = normalize_regex(m.group(1))
                if s and not s.isdigit():
                    rx.add(s)
        if f is helpers_path:
            continue
        for helper, idxs in npos.items():
            for m in re.finditer(r"\b" + re.escape(helper) + r"\s*\(", t):
                args, _ = split_args(t, m.end() - 1)
                for i in idxs:
                    if i < len(args):
                        q, r = literals_in(args[i])
                        quoted |= q
                        rx |= r

    quoted = {u for u in quoted if u}
    rx = {u for u in rx if u} - quoted
    allused = quoted | rx
    meta = {
        "helpers_total": len(funcs),
        "helpers_name_taking": {k: sorted(v) for k, v in sorted(npos.items())},
        "n_quoted": len(quoted),
        "n_regex": len(rx),
        "spec_files": len(specs),
    }
    return ({u.lower() for u in allused}, {u.lower() for u in quoted}, meta)


def decode_escapes(s: str) -> str:
    """还原 JS 源码里的 \\uXXXX 转义（否则 `\\u4e0a\\u6d77` 无法与需求文本比对）。"""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def app_doc_text(app_dir: pathlib.Path) -> str:
    """该 app 需求目录下全部文本，小写——用于判定「这个用名需求里到底有没有」。"""
    req_dir = app_dir / "requirements"
    parts = []
    for f in sorted(req_dir.rglob("*")):
        if f.is_file() and f.suffix.lower() in (".yaml", ".yml", ".txt", ".md", ".json"):
            parts.append(f.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts).lower()


def main() -> int:
    print("=" * 116)
    print("可访问名抽检 · 子串口径 · 地面真值 = 测试文件里实际用到的字面量（引号名 + 正则名）")
    print("=" * 116)
    print(f"{'app':14s} {'生成单元':>8s} {'交互单元':>8s} {'0名(全部)':>9s} {'空转率':>7s} "
          f"{'0名(交互)':>9s} {'交互空转率':>10s} {'抽取条数':>8s} {'去重名':>7s} "
          f"{'测试用名':>8s} {'命中':>5s} {'召回下界':>8s}")
    print("  生成单元 = 有场景的叶子（生成与测试的单位）")
    print(f"  交互单元 = 场景名或任一步骤含 {'/'.join(INTERACTION_VERBS)} 的单元 ← 验收口径（PLAN.md §4.1 裁定）")
    print("  0名单元 = 该单元在需求里一个可访问名都抽不到 → L1 闸门对它完全空转")
    print("  召回下界 = 把「未命中且需求文本里出现过」的项全部当作真缺口（保守下界）")
    print("-" * 116)

    summary = {}
    for app in APPS:
        app_dir = BENCH / app
        tree, _ = load_requirement_tree(app_dir)
        index = extract_accessible_names(tree)
        by_req = index.by_req()
        extracted = {n.strip().lower() for n in index.unique_names()}

        units = [n for n in tree.leaves() if n.scenarios]
        empty_units = [n for n in units if not by_req.get(n.id)]
        empty_rate = len(empty_units) / max(1, len(units))

        iunits = [n for n in units if is_interaction_unit(n)]
        iempty = [n for n in iunits if not by_req.get(n.id)]
        iempty_rate = len(iempty) / max(1, len(iunits))

        used, _quoted_used, meta = collect_used(app_dir)
        doc = app_doc_text(app_dir)
        hit = {u for u in used if any(u in x or x in u for x in extracted)}
        gap_a = {u for u in (used - hit) if u in doc}
        gap_b = {u for u in (used - hit) if u not in doc}
        cov = len(hit) / max(1, len(used))
        cov_floor = len(hit) / max(1, len(hit) + len(gap_a))

        summary[app] = {
            "units": len(units), "units_without_names": len(empty_units),
            "empty_rate": empty_rate,
            "interaction_units": len(iunits),
            "interaction_units_without_names": len(iempty),
            "interaction_empty_rate": iempty_rate,
            "empty_unit_ids": [n.id for n in empty_units],
            "interaction_empty_unit_ids": [n.id for n in iempty],
            "entries": len(index.entries), "extracted": len(extracted),
            "used": len(used), "hit": len(hit), "coverage": cov,
            "coverage_floor": cov_floor,
            "gap_a": sorted(gap_a), "gap_b": sorted(gap_b), **meta,
        }
        print(f"{app:14s} {len(units):8d} {len(iunits):8d} {len(empty_units):9d} "
              f"{empty_rate*100:6.0f}% {len(iempty):9d} {iempty_rate*100:9.0f}% "
              f"{len(index.entries):8d} {len(extracted):7d} {len(used):8d} "
              f"{len(hit):5d} {cov_floor*100:7.0f}%")

    print()
    for app in APPS:
        s = summary[app]
        print(f"--- {app}: 交互单元空转 {s['interaction_units_without_names']}/{s['interaction_units']}"
              f" = {s['interaction_empty_rate']*100:.0f}%   （全部单元 {s['units_without_names']}/{s['units']}"
              f" = {s['empty_rate']*100:.0f}%）")
        if s["interaction_empty_unit_ids"]:
            ids = ", ".join(s["interaction_empty_unit_ids"][:16])
            print(f"    抽不到名字的交互单元：{ids}"
                  f"{' …' if len(s['interaction_empty_unit_ids']) > 16 else ''}")
        print(f"    未命中 {len(s['gap_a'])+len(s['gap_b'])} 个，其中需求文本里出现过的 {len(s['gap_a'])} 个"
              f"（真缺口上界）：")
        for m in s["gap_a"][:10]:
            print(f"      ✗ {m[:92]}")
        if len(s["gap_a"]) > 10:
            print(f"      … 其余 {len(s['gap_a'])-10} 个")
        print()

    print("=" * 116)
    print("机器可读摘要（**interaction_* 是验收口径**，其余为诊断）：")
    print({a: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()
               if k in ("units", "units_without_names", "empty_rate",
                        "interaction_units", "interaction_units_without_names",
                        "interaction_empty_rate", "entries",
                        "extracted", "used", "hit", "coverage", "coverage_floor",
                        "n_quoted", "n_regex")}
           for a, s in summary.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
