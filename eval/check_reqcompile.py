#!/usr/bin/env python3
"""reqcompile 验收脚本 —— 第 1 阶段的回归检查。

存在理由：需求编译是纯代码、零 LLM 调用的阶段，**它的输出是后面所有阶段
的靶子**。靶子 quietly 退化（少抽一个可访问名、漏一个场景）不会报错，
只会让生成的东西挂不上外部测试。所以每次改 reqcompile 都跑一遍这个。

检三件事：

1. **六个赛题 + quickstart 都能编译**（含 2 个坏 YAML 的修复）
2. **12306 的标题契约仍然 1:1**（需求场景 == 真实 test() 标题，双向零差）
3. **可访问名抽取对 12306 的覆盖率不退化**（当前 98%，子串口径）

用法：
    python eval/check_reqcompile.py

退出码：全绿 0，任一检查失败 1。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from reqcompile import (  # noqa: E402
    build_scenario_index,
    extract_accessible_names,
    load_requirement_tree,
)

BENCH = ROOT / "repos" / "arc-bench" / "arc-bench" / "webapp"
QUICKSTART = (ROOT / "repos" / "agentic-requirement-compiler"
              / "example" / "ticketbooking-quickstart")

APPS = ["12306", "bookstack", "ctrip", "keep", "prestashop", "stackoverflow"]

# 已知必须被修复才能解析的文件（坏 YAML）。数字变了要有人看一眼。
EXPECTED_REPAIRS = {"bookstack": 2, "keep": 1}

# 12306 可访问名覆盖率下限（子串口径）。实测 98%，留 2 个百分点余量。
A11Y_COVERAGE_FLOOR = 0.95

failures: list[str] = []


def check_compile_all() -> dict[str, dict]:
    print("=" * 78)
    print("检查 1/3 —— 六个赛题 + quickstart 全部可编译")
    print("=" * 78)
    print(f"{'app':16s} {'节点':>5s} {'叶子':>5s} {'场景':>5s} {'可访问名':>7s} "
          f"{'修复':>5s} {'错误':>5s} {'警告':>5s}")
    print("-" * 78)

    results: dict[str, dict] = {}
    targets = [(a, BENCH / a) for a in APPS] + [("quickstart", QUICKSTART)]

    for name, path in targets:
        try:
            tree, report = load_requirement_tree(path)
            index = build_scenario_index(tree)
            a11y = extract_accessible_names(tree)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:16s} ❌ {type(exc).__name__}: {str(exc)[:44]}")
            failures.append(f"{name}: 无法编译 —— {exc}")
            continue

        repairs = sum(len(r.repairs) for r in report.repairs)
        results[name] = {
            "nodes": len(tree.nodes),
            "leaves": len(tree.leaves()),
            "scenarios": len(index.entries),
            "a11y": len(a11y.entries),
            "repairs": repairs,
            "errors": len(report.errors),
        }
        flag = "❌" if report.errors else "  "
        print(f"{name:16s} {len(tree.nodes):5d} {len(tree.leaves()):5d} "
              f"{len(index.entries):5d} {len(a11y.entries):7d} {repairs:5d} "
              f"{len(report.errors):5d} {len(report.warnings):5d} {flag}")

        if report.errors:
            failures.append(f"{name}: {len(report.errors)} 个校验错误")

        # 坏 YAML 必须仍然被修好，且修复数符合预期
        if name in EXPECTED_REPAIRS and repairs != EXPECTED_REPAIRS[name]:
            failures.append(
                f"{name}: 预期修 {EXPECTED_REPAIRS[name]} 处，实际 {repairs} 处"
            )

    print()
    return results


def check_title_contract() -> None:
    """需求场景名 → 测试标题，必须与真实 test() 1:1。"""
    print("=" * 78)
    print("检查 2/3 —— 12306 标题契约（需求场景 vs 真实 test() 标题）")
    print("=" * 78)

    app_dir = BENCH / "12306"
    tree, _ = load_requirement_tree(app_dir)
    index = build_scenario_index(tree)

    expected = {f"{e.req_id}: {e.name}" for e in index.entries}

    actual: set[str] = set()
    for spec in sorted((app_dir / "tests").glob("*.spec.ts")):
        actual |= set(re.findall(r"test\(\s*'([^']+)'",
                                 spec.read_text(encoding="utf-8")))

    print(f"  需求侧场景 : {len(expected)}")
    print(f"  测试侧 test: {len(actual)}")
    print(f"  交集       : {len(expected & actual)}")
    print(f"  只在一侧   : {len(expected - actual)} / {len(actual - expected)}")
    if expected != actual:
        for t in sorted(expected - actual)[:5]:
            print(f"    只在需求侧: {t[:100]}")
        for t in sorted(actual - expected)[:5]:
            print(f"    只在测试侧: {t[:100]}")
        failures.append("12306 标题契约不再 1:1")
    else:
        print("  ✅ 双向零差 —— 「测试标题 = {req_id}: {scenario_name}」成立")
    print()


def check_a11y_coverage() -> None:
    """可访问名抽取对真实测试用名的覆盖率（子串口径）。

    用子串口径是因为 Playwright 的 `getByRole({name})` / `getByLabel` 默认
    就是**大小写不敏感的子串匹配**——所以"我们抽到的名字是测试所用名字的
    子串或超串"才算真正可用。
    """
    print("=" * 78)
    print("检查 3/3 —— 12306 可访问名覆盖率（子串口径，模拟 Playwright 匹配）")
    print("=" * 78)

    app_dir = BENCH / "12306"
    tree, _ = load_requirement_tree(app_dir)
    extracted = {n.strip().lower() for n in extract_accessible_names(tree).unique_names()}

    used: set[str] = set()
    files = list((app_dir / "tests").glob("*.spec.ts")) + [app_dir / "tests" / "helpers.ts"]
    patterns = [
        r"clickNamed\(\s*page\s*,\s*'([^']+)'",
        r"getByLabel\(\s*'([^']+)'",
        r"fillField\(\s*page\s*,\s*'([^']+)'",
        r"setCheckboxByText\(\s*page\s*,\s*'([^']+)'",
        r"selectOption\(\s*page\s*,\s*'[^']+'\s*,\s*'([^']+)'",
        r"getByRole\(\s*'[a-z]+'\s*,\s*\{\s*name:\s*'([^']+)'",
    ]
    for f in files:
        t = f.read_text(encoding="utf-8")
        for p in patterns:
            used |= set(re.findall(p, t))

    # 这三个是 helpers 里的固定字段名，不是可访问名，排除掉免得虚高
    used = {u.strip().lower() for u in used if u.strip()} - {"from", "to", "date"}

    hit = {u for u in used if any(u in x or x in u for x in extracted)}
    cov = len(hit) / max(1, len(used))

    print(f"  抽取去重名 : {len(extracted)}")
    print(f"  测试用名   : {len(used)}")
    print(f"  覆盖       : {len(hit)}/{len(used)} = {cov*100:.0f}%")
    miss = sorted(used - hit)
    if miss:
        print(f"  未覆盖 {len(miss)} 个（裸可见文本，需求里没有引号，抽不到是预期的）:")
        for m in miss[:6]:
            print(f"     - {m[:90]}")

    if cov < A11Y_COVERAGE_FLOOR:
        failures.append(f"可访问名覆盖率 {cov*100:.0f}% 低于下限 {A11Y_COVERAGE_FLOOR*100:.0f}%")
        print(f"  ❌ 低于下限 {A11Y_COVERAGE_FLOOR*100:.0f}%")
    else:
        print(f"  ✅ 不低于下限 {A11Y_COVERAGE_FLOOR*100:.0f}%")
    print()


def main() -> int:
    check_compile_all()
    check_title_contract()
    check_a11y_coverage()

    print("=" * 78)
    if failures:
        print(f"❌ 失败 {len(failures)} 项：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ reqcompile 验收全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
