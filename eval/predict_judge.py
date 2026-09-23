#!/usr/bin/env python3
"""阶梯 ③：判分前的预测（**逐 spec 预测通过/失败 + 必须打印覆盖率**，`PLAN.md` §7 ③；零 token）。

**它省的是墙钟**（30–135 分钟/次）：`score_app.sh` 的冒烟只挡"**起不来**"，
挡不了"**起来了但名字对不上**"——而后者正是 `keep` 那轮 31 条失败的形态。

⚠️ **它现在是"信息"，不是"闸门"**（2026-09-22，第二十五轮审核 §三）：`PLAN` §7 ③ 的阈值
（"预测失败 = 0 才送判分"）**比闸门真正要求的更严**（闸门要的是"非零"）→ 复核前**不要用它挡判分**。
先用**结果已知**的产物标定它（`--truth runs/<record>.json`），看"预测失败"与"真的失败"差多少。

⚠️ **覆盖率必须每次打**（纪律 15 的原型）：解析器**已知会漏**——低覆盖率会把"漏抽"
伪装成"没有缺失"。所以本工具每次都打"本 spec 共 N 处定位调用、解析器认了 M 处"，
并把**未识别的调用原文**列出来让人看一眼。

用法：
    python eval/predict_judge.py <app> <artifact> <REQ ids...>              # 预测
    python eval/predict_judge.py keep <artifact> REQ-2.1 REQ-2.2 \
        --truth runs/20260922T090406Z-keep-score-r4-keep4b.json             # 标定（混淆矩阵）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
import testspec as ts  # noqa: E402

FRONTEND_EXT = {".ts", ".tsx", ".js", ".jsx", ".html", ".css"}
# 这些 kind 的名字**必须能在前端源码里找到**（否则判据大概率找不到它）
NEED_KINDS = {"role", "label", "text", "title", "placeholder", "testid", "css",
              "arg:need", "arg(re):need", "fixture:need"}


# ---- 「locator 可命中的位置」----------------------------------------------------
# 为什么必须有（第二十八轮审核 §三 A）：**裸的源码子串**不等于"判据能命中它"。
# 实测那次漏报：`/header/i` 在产物里只出现在 `<header>` **标签名**与 axios 的 `headers:` **JS 键**
# 里（两者都不是文本、也不是可访问名）→ 判据 `getByRole(..., {name})` / `getByText` 都找不到它，
# 而"源码子串"检查却说"在" → **③ 预测通过、实际挂**。
# → 判据要的是"**能被某个 locator 命中**"，所以只在下面这几类位置里找：
#     ① JSX 文本节点（`>Sidebar<`）      ② `aria-label="…"`     ③ `title="…"`
#     ④ `placeholder="…"`               ⑤ `name="…"`（表单字段名）
# 静态检查做不到"真的跑一遍 locator 解析"，但"必须落在这些属性/文本里"已经把
# 最危险的方向（**漏报**）收窄了 —— 反例仍存在（见文件末尾的已知边界）。
RE_TEXT_NODE = re.compile(r">([^<>{}]{2,120})<")
RE_ATTR_HITTABLE = re.compile(
    r"\b(?:aria-label|title|placeholder|name|alt)\s*=\s*(?:"
    r"\"([^\"]{2,120})\"|'([^']{2,120})'|\{\s*[\"'`]([^\"'`]{2,120})[\"'`]\s*\})")
# 某些写法是"值来自变量/模板串"（`aria-label={label}`）→ 静态拿不到值，
# 记一条**不可判**的证据，别默默当成"没有"（纪律 15：低覆盖不许伪装成"没有缺失"）。
RE_ATTR_EXPR = re.compile(r"\b(?:aria-label|title|placeholder|name|alt)\s*=\s*\{\s*(?![\"'`])")


def hittable_strings(blob: str) -> list[str]:
    """产物里**能被 locator 命中**的字符串（文本节点 + 可访问名相关属性）。"""
    out = [m.group(1).strip() for m in RE_TEXT_NODE.finditer(blob)]
    for m in RE_ATTR_HITTABLE.finditer(blob):
        out.append((m.group(1) or m.group(2) or m.group(3) or "").strip())
    return [s for s in out if s]


def frontend_blob(artifact: Path) -> str:
    front = artifact / "frontend/src"
    if not front.is_dir():
        return ""
    return "\n".join(f.read_text(encoding="utf-8", errors="replace")
                     for f in front.rglob("*") if f.is_file() and f.suffix in FRONTEND_EXT)


def missing_targets(targets: list[ts.Target], blob: str) -> list[ts.Target]:
    """产物里**命中不了**的名字。

    判据分两类（第二十八轮审核 §三 A 定的）：
      · **loose/正则靶子**（`arg(re)` 那种，值是 `toPattern()` 出来的正则）：
        只在**可命中的位置**里按正则匹配 —— **不接受裸的源码子串命中**；
      · 其余具名靶子：沿用"源码里出现过"（它们本来就是**精确名**，判据按名找，
        而精确名出现在源码里的位置通常就是它被渲染的地方）。
      `css` 靶子单独处理：取类名/标签名，在源码里查（它本来就是选择器，不是可访问名）。
    """
    hittable = hittable_strings(blob)
    low_hit = [s.lower() for s in hittable]
    low_all = blob.lower()
    out = []
    for t in targets:
        if t.kind not in NEED_KINDS or not t.name:
            continue
        needle = t.name.strip()
        if t.kind == "css":
            needle = needle.lstrip(".#").split(":")[0]
            if len(needle) >= 2 and needle.lower() not in low_all:
                out.append(t)
            continue
        if len(needle) < 2:
            continue
        if "arg(re)" in t.kind:                     # loose：必须落在可命中的位置
            try:
                pat = re.compile(needle, re.I)
            except re.error:
                pat = re.compile(re.escape(needle), re.I)
            if not any(pat.search(s) for s in hittable):
                out.append(t)
            continue
        if needle.lower() not in low_all:           # 精确名：沿用"源码里出现过"
            out.append(t)
    return out, hittable, bool(RE_ATTR_EXPR.search(blob))


def load_truth(record_path: Path) -> dict[str, bool]:
    """从 RunRecord 取"每个 spec 的真值"（`per_test_passed` / `per_test_failed`）。"""
    d = json.loads(record_path.read_text(encoding="utf-8"))
    truth: dict[str, bool] = {}
    for key, ok in (("per_test_passed", True), ("per_test_failed", False)):
        for item in (d.get(key) or []):
            tid = item.get("test_id") or ""
            spec = tid.split(":")[0]
            if spec:
                truth[spec] = ok        # 同一 spec 多条时后者覆盖：只用于**粗标定**
    return truth


def main() -> int:
    argv = [a for a in sys.argv[1:]]
    truth_path = None
    if "--truth" in argv:
        i = argv.index("--truth")
        truth_path = Path(argv[i + 1])
        del argv[i:i + 2]
    if len(argv) < 3:
        print(__doc__)
        return 2
    app, artifact = argv[0], Path(argv[1])
    subset = argv[2:]
    if not artifact.is_absolute():
        artifact = ROOT / artifact

    d = ROOT / "repos" / "arc-bench" / "arc-bench" / "webapp" / app
    htext = ts.helpers_path(d).read_text(encoding="utf-8", errors="replace")
    bodies = ts.helper_bodies(htext)
    blob = frontend_blob(artifact)
    print("=" * 78)
    print(f"判分前预测（app={app}  子集={subset}）")
    print(f"  产物：{artifact}")
    print(f"  前端源码：{len(blob.splitlines())} 行{'（⚠️ 没找到 frontend/src！）' if not blob else ''}")
    print("=" * 78)
    if not blob:
        print("产物里没有 frontend/src —— 先确认产物目录对不对")
        return 1

    rows = []
    cov_all = ts.Coverage()
    hittable_seen: set[str] = set()   # 产物里能被 locator 命中的字符串（口径见 missing_targets）
    expr_attr = False                 # 有没有 `aria-label={表达式}` 这类静态拿不到值的写法
    unrecognized: list[str] = []
    for spec in ts.spec_files(d, subset):
        targets, cov, used = ts.closure_for_spec(spec, htext, bodies)
        cov_all.add(cov)
        unrecognized += ts.unrecognized_snippets(spec.read_text(encoding="utf-8", errors="replace"))
        miss, hittable, has_expr = missing_targets(targets, blob)
        hittable_seen.update(hittable)
        expr_attr = expr_attr or has_expr
        rows.append((spec, cov, len(targets), miss))

    print(f"{'spec':34s} {'预测':6s} {'闭包靶子':>8s} {'需显示':>6s}  覆盖率")
    for spec, cov, n_t, miss in rows:
        need_n = len([t for t in ts.closure_for_spec(spec, htext, bodies)[0] if t.kind in NEED_KINDS and t.name])
        print(f"  {spec.name:32s} {'✅通过' if not miss else '❌失败':6s} {n_t:>8d} {need_n:>6d}  {cov}")

    print("\n── 预测失败的 spec 与缺的靶子 ──")
    any_miss = False
    for spec, cov, n_t, miss in rows:
        if not miss:
            continue
        any_miss = True
        print(f"  ❌ {spec.name}（{len(miss)} 个缺）")
        for t in miss[:8]:
            print(f"       · [{t.kind}{'/' + t.role if t.role else ''}] {t.name!r}  ← {t.where}")
    if not any_miss:
        print("  （无 —— 闭包里的名字都能在产物里找到）")

    print(f"\n── 覆盖率（**必看**：低 M 会把「漏抽」伪装成「没有缺失」）──")
    print(f"  合计：{cov_all}")
    print(f"  可命中位置：{len(hittable_seen)} 条字符串（文本节点 + aria-label/title/placeholder/name/alt）"
          + ("；⚠️ 有 `aria-label={表达式}` 这类静态拿不到值的写法 → loose 靶子可能被误判为不可达"
             if expr_attr else ""))
    if unrecognized:
        print(f"  ⚠️ 未识别的定位调用 {len(unrecognized)} 处（前 5）：")
        for s in unrecognized[:5]:
            print(f"       · {s}")

    pred_fail = [spec.name for spec, _c, _n, miss in rows if miss]
    print(f"\n── 汇总 ──")
    print(f"  预测通过 {len(rows) - len(pred_fail)} / 预测失败 {len(pred_fail)}"
          f"（子集共 {len(rows)} 条）")
    print(f"  ⚠️ 阈值提醒：§7 ③ 的「预测失败 = 0 才送判分」**比闸门更严**（闸门要的是**非零**）——"
          "复核前只用它决定「要不要先修」，**不要用它挡住判分**。")

    if truth_path:
        truth = load_truth(truth_path if truth_path.is_absolute() else ROOT / truth_path)
        print(f"\n── 标定（真值来自 {truth_path.name}）──")
        tp = fp = tn = fn = 0
        for spec, _c, _n, miss in rows:
            real_passed = truth.get(spec.name)      # True/False/None（None = 记录里没有，不参与）
            if real_passed is None:
                continue
            predicted_fail = bool(miss)
            actual_failed = not real_passed
            if predicted_fail and actual_failed:
                tp += 1
            elif predicted_fail and not actual_failed:
                fp += 1
            elif not predicted_fail and actual_failed:
                fn += 1
            else:
                tn += 1
        print(f"  真值覆盖 {tp + fp + tn + fn} / {len(rows)} 个 spec"
              f"（RunRecord 里没出现的算未知，不参与）")
        print("                    真·失败   真·通过")
        print(f"    预测失败         {tp:>3d}      {fp:>3d}     （TP / FP=「多修」）")
        print(f"    预测通过         {fn:>3d}      {tn:>3d}     （FN=「漏报」，最危险 / TN）")
        prec = f"{tp / (tp + fp) * 100:.0f}%" if (tp + fp) else "—"
        rec = f"{tp / (tp + fn) * 100:.0f}%" if (tp + fn) else "—"
        print(f"  粗判：精度={prec}（预测失败里真失败的占比）  召回={rec}（真失败里被预测到的占比）")
        spec_n = tp + fp + tn + fn
        print(f"  ⚠️ 样本量：**spec N={spec_n} / 产物 N=1** —— 同一份产物里的各 spec 是**相关**的，"
              "所以别把 spec 数当成独立观测数；先只当「方向」看，要定阈值得攒 **≥3 份产物**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
