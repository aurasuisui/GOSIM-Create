#!/usr/bin/env python3
"""阶梯 ②：子集靶子闭包（**三步闭合**，`PLAN.md` §7 ②；零 token）。

**它解决什么**：`keep` 那轮 31 条失败**全是「名字/角色对不上」**，而「判据要什么名字」这件事
**写在测试与 `helpers.ts` 里**——所以「这个子集能不能过」在判分前就能算。
`keep` 的 `REQ-6.2` 就是漏了这一算：8/32 个 spec 调 `Toggle sidebar`，
而它由**不在子集里**的 REQ 命名 → 那 14 条断言必挂。

**三步**（与 §7 ② 一一对应）：
  1. **数候选 REQ 的 spec 调用了几个 helper** —— 越少越「入门」（`keep` 的 `REQ-2.1` 只用 2 个）；
  2. **取闭包** —— 这些 helper 的函数体（含互相调用）里出现的定位器 + **调用方传进去的字符串**；
  3. **对表** —— 闭包里每个靶子：需求文本点名了它吗（`grep -i`，记行号），
     **没点到 → 必须显式进该 app 的生成硬清单**。**未闭合 = 不许开工。**

用法：
    python eval/subset_closure.py <app> [REQ ids...]      # 不给子集 → 只打「入门度」排行
    python eval/subset_closure.py bookstack REQ-4.1 REQ-5.1
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import testspec as ts  # noqa: E402

# 只对**具体靶子**对表：(var) 那几种的名字来自调用方，具体值在 arg/fixture 里（已单列）
CONCRETE_KINDS = {"role", "label", "text", "title", "placeholder", "testid", "css",
                  "fixture", "arg", "arg(re)",
                  "fixture:need", "arg:need", "arg(re):need",
                  "fixture:input", "arg:input", "arg(re):input",
                  "fixture:unknown", "arg:unknown", "arg(re):unknown",
                  "fixture?", "fixture?:need", "fixture?:input", "fixture?:unknown"}
# 只有这几类才"必须在产物里显示"；`input` 是**测试自己敲进去的值**，不是产物要先有的内容
MUST_DISPLAY = {"role", "label", "text", "title", "placeholder", "testid", "css"}


def _bucket(kind: str) -> str:
    if kind in MUST_DISPLAY or kind.endswith(":need") or kind == "fixture?":
        return "need"
    if kind.endswith(":input"):
        return "input"
    if kind in ("fixture", "arg", "arg(re)"):
        return "need"          # 旧口径（没带动词分类）保守算"需要显示"
    return "other"


def app_dir(app: str) -> Path:
    return ROOT / "repos" / "arc-bench" / "arc-bench" / "webapp" / app


def requirement_texts(app: str) -> list[Path]:
    """需求文本（.yaml + .md 都查——纪律 17：不能只搜一种）。"""
    d = app_dir(app) / "requirements"
    out: list[Path] = []
    if d.is_dir():
        out += sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")) + sorted(d.glob("*.md"))
    return out


def covered_in_requirements(name: str, files: list[Path]) -> list[str]:
    """按项目纪律查：`grep -i` + 同时查 .yaml/.md + **记行号**（全量需求文本）。

    返回命中列表（`文件:行号: 该行片段`）；空列表 = **需求文本里一次都没查到**。
    """
    hits: list[str] = []
    if not name or len(name) < 2:
        return hits
    needle = name.lower()
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if needle in line.lower():
                hits.append(f"{f.name}:{i}: {line.strip()[:96]}")
    return hits


def subset_texts(app: str, req_ids: list[str]) -> dict[str, str]:
    """**本子集**自己的需求文本（按节点取：description + 场景步骤）。

    为什么不能用"全量需求文本"判覆盖（`keep` 的 REQ-6.2 就是这么漏的）：
    生成时模型只看到**子集**那份 brief —— 一个名字若只出现在**别的 REQ** 的文本里，
    对这次生成等于**没说**。所以覆盖判据必须是"**本子集的文本里点名了它**"。
    """
    if not req_ids:
        return {}
    sys.path.insert(0, str(ROOT / "pipeline"))
    try:
        from reqcompile.loader import load_requirement_tree
    except Exception as exc:  # noqa: BLE001
        print(f"    ⚠️ 载入需求树失败（{exc!r}）→ 覆盖率判据退化为全量文本")
        return {}
    p = app_dir(app) / "requirements" / "requirements.yaml"
    if not p.is_file():
        return {}
    tree, _report = load_requirement_tree(p)
    wanted = set(req_ids)
    out: dict[str, str] = {}
    for n in tree.ordered():
        if n.id not in wanted:
            continue
        parts = [n.description or ""]
        for sc in (n.scenarios or []):
            for st in (sc.steps or []):
                parts.append((st.content or "") + " " + (st.keyword or ""))
        out[n.id] = " ".join(parts)
    return out


def covered_in_subset(name: str, texts: dict[str, str]) -> list[str]:
    """名字在**本子集**的哪条需求文本里出现过（返回 `REQ-x（命中 n 次）`）。"""
    hits: list[str] = []
    if not name or len(name) < 2:
        return hits
    needle = name.lower()
    for rid, text in texts.items():
        c = text.lower().count(needle)
        if c:
            hits.append(f"{rid}（命中 {c} 次）")
    return hits


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python eval/subset_closure.py <app> [REQ ids...]")
        return 2
    app = sys.argv[1]
    subset = [a for a in sys.argv[2:] if a.strip()]
    d = app_dir(app)
    if not (d / "tests").is_dir():
        print(f"找不到 {app} 的 tests/：{d}")
        return 1

    hpath = ts.helpers_path(d)
    htext = hpath.read_text(encoding="utf-8", errors="replace") if hpath.is_file() else ""
    bodies = ts.helper_bodies(htext)
    print("=" * 78)
    print(f"子集闭包（app={app}，子集={subnet if (subnet := subset) else '（未指定 → 只打入门度）'}）")
    print("=" * 78)

    # ---- [1] 入门度：每个候选 REQ 的 spec 调用了几个 helper ----
    print('\n[1] 候选 REQ 的 spec 调用了几个 helper（**越少越「入门」**）')
    print(f"    {'REQ/spec':34s} {'spec 自己的定位器':>14s} {'调用的 helper':>12s}   helper 名")
    infos = {}
    for spec in ts.spec_files(d):
        own, own_cov = ts.locators(spec.read_text(encoding="utf-8", errors="replace"), spec.name)
        called = sorted({m.group(1) for m in ts.RE_H_CALL.finditer(spec.read_text(encoding="utf-8", errors="replace"))})
        infos[spec.name] = (own_cov.total, len(called))
        print(f"    {spec.name:34s} {own_cov.total:>14d} {len(called):>12d}   {', '.join(called[:4])}{'…' if len(called) > 4 else ''}")

    # ---- [2]+[3] 闭包与对表 ----
    specs = ts.spec_files(d, subset) if subset else []
    if not subset:
        print("\n（未给子集 → 到此为止。挑子集时用上表的 helper 数作「入门度」，再跑一次本脚本对表。）")
        return 0

    print(f"\n[2]+[3] 闭包与对表（{len(specs)} 个 spec）")
    files = requirement_texts(app)
    stexts = subset_texts(app, subset)
    print(f"    需求文本：{[f.name for f in files] or '（没找到需求文件）'}")
    print(f"    覆盖判据 = **本子集自己的文本**：{[f'{k}({len(v)} 字)' for k, v in stexts.items()] or '（没取到 → 退化为全量文本）'}")
    cov_all = ts.Coverage()
    uncovered_all: dict[str, ts.Target] = {}
    covered_all: dict[str, ts.Target] = {}
    for spec in specs:
        targets, cov, used = ts.closure_for_spec(spec, htext, bodies)
        cov_all.add(cov)
        concrete = [t for t in targets if t.kind in CONCRETE_KINDS and t.name]
        uncovered = []
        # **析取组**（mode=any，`expectAnyVisible([a,b,c])`）：组内任一个被需求点名即算覆盖
        any_groups: dict[str, list] = {}
        for t in concrete:
            if t.mode == "any":
                any_groups.setdefault(t.group or t.where, []).append(t)   # 按「要求单元」分组
        group_ok: dict[str, bool] = {}
        for where_, group in any_groups.items():
            group_ok[where_] = any(
                (covered_in_subset(x.name, stexts) if stexts else covered_in_requirements(x.name, files))
                for x in group)
        for t in concrete:
            key = f"{t.kind}:{t.name}"
            if t.mode == "any":
                in_subset = group_ok.get(t.group or t.where, False)
            else:
                in_subset = (covered_in_subset(t.name, stexts) if stexts else covered_in_requirements(t.name, files))
            (covered_all if in_subset else uncovered_all)[key] = t
            if key in uncovered_all:
                uncovered.append(t)
                # 全量里有、子集里没有 → 那正是 keep 的 REQ-6.2 形态，必须标出来
                if stexts and covered_in_requirements(t.name, files):
                    t.where += " ⚠️ 全量需求里出现过、但**不在本子集**（生成时模型看不到 → 等于没说）"
        print(f"\n  ── {spec.name}  闭包 {len(targets)} 个靶子（具体 {len(concrete)}）  "
              f"helper {len(used)} 个  覆盖率 {cov}")
        if targets and not concrete:
            print("      ⚠️ 具体靶子 0 个但闭包非空 —— 先查实参抽取（正则字面量/变量名都可能漏）")
        print(f"     未被需求覆盖 {len(uncovered)} 个")
        for t in uncovered[:8]:
            tag = " ⚠️任一即可（析取组）" if t.mode == "any" else ""
            print(f"       ✗ [{t.kind}{'/' + t.role if t.role else ''}] {t.name!r}  ← {t.where}{tag}")
        if len(uncovered) > 8:
            print(f"       … 其余 {len(uncovered) - 8} 个")

    print("\n" + "=" * 78)
    print("[4] 汇总")
    print(f"    覆盖率（解析器认了 M / 共 N）：{cov_all}   ← **低 M 会把「漏抽」伪装成「没有缺失」**")
    buckets: dict[str, dict[str, ts.Target]] = {"need": {}, "input": {}, "other": {}}
    merged: dict[str, ts.Target] = {}
    # 同名靶子可能以两种形态出现（spec 级的裸 `fixture` 与带动词分类的 `fixture:need`）→
    # **优先保留带分类的那个**，否则同一个值会被打印两遍（实测：keep 的 'Weekend plan'）
    for key, tg in {**covered_all, **uncovered_all}.items():
        prev = merged.get(tg.name)
        if prev is None or (":" in tg.kind and ":" not in prev.kind):
            merged[tg.name] = tg
    uncovered_names = {tg.name for tg in merged.values() if tg.name not in
                       {t.name for t in covered_all.values()}}
    for tg in merged.values():
        buckets[_bucket(tg.kind)][tg.name] = tg
    uncovered_all = {k: v for k, v in merged.items() if v.name in uncovered_names}
    print(f"    闭包里的具体靶子：{sum(len(b) for b in buckets.values())} 个"
          f"（需要显示 {len(buckets['need'])} / 测试自己输入 {len(buckets['input'])}"
          f" / 未分类 {len(buckets['other'])}）")

    print("\n    🔴 **必须在产物里显示**、而需求文本没点到 → 进该 app 的生成硬清单：")
    need_uncov = {k: v for k, v in uncovered_all.items() if _bucket(v.kind) == "need"}
    if need_uncov:
        for key, t in sorted(need_uncov.items()):
            print(f"       · [{t.kind}{'/' + t.role if t.role else ''}] {t.name!r}"
                  f"   （族={t.family or '?'}；来自 {t.where}）")
    else:
        print("       （空 —— 需要显示的靶子都被需求文本点名了）")

    print("\n    🟡 测试会**自己输入**的值（产物不必预先包含；但表单要能接住、并在页面上显示出来）：")
    if buckets["input"]:
        for key, t in sorted(buckets["input"].items()):
            print(f"       · [{t.kind}] {t.name!r}   （族={t.family or '?'}；来自 {t.where}）")
    else:
        print("       （空）")

    if buckets["other"]:
        print("\n    ❓ 无法分类（helper 名不在动词表里）→ **人工看一眼**，别默默当没事：")
        for key, t in sorted(buckets["other"].items()):
            print(f"       · [{t.kind}] {t.name!r}   （族={t.family or '?'}；来自 {t.where}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
