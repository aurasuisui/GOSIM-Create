#!/usr/bin/env python3
"""§4.1 第 5 条规则的**精度/召回探针**（零 token）。

裁定给了三个约束，这是**约束 2（必须同时报精度）**的落地：
**只报召回一定虚高**——地面真值里本身就混着非可访问名，而且"召回上升"可能只是拟合上了噪声。

用法：
    python eval/a11y_prose_probe.py keep            # 打 20 条样本供人工判精度 + 量召回
    python eval/a11y_prose_probe.py keep --n 30
    python eval/a11y_prose_probe.py --all           # 六个 app 的散文份额与空转率

召回是**测量**，不是抽取依据：平台上 agent 看不到测试，测试只当**地面真值**做校准
（`reviews/2026-09-21-第十五轮审核.md` §3.1 点名要求）。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from reqcompile import extract_accessible_names, load_requirement_tree   # noqa: E402

BENCH = ROOT / "repos" / "arc-bench" / "arc-bench" / "webapp"
APPS = ["12306", "bookstack", "ctrip", "keep", "prestashop", "stackoverflow"]


def _test_targets(app_dir: pathlib.Path) -> set[tuple[str, str]]:
    """从判据里取 (role, name) —— **只用于测量**，绝不进抽取路径。"""
    out: set[tuple[str, str]] = set()
    for spec in app_dir.glob("tests/*.ts"):
        text = spec.read_text(encoding="utf-8", errors="replace")
        for role, name in re.findall(
                r"getByRole\(\s*'([a-z]+)'\s*,\s*\{\s*name:\s*/([^/]+)/", text):
            out.add((role, name.strip().strip("^$").strip()))
        for role in re.findall(r"getByRole\(\s*'([a-z]+)'\s*\)", text):
            out.add((role, ""))                       # 纯 role 定位
    return out


def _matches(role: str, name: str, target: tuple[str, str]) -> bool:
    trole, tname = target
    if role and trole and role != trole:
        return False
    if not tname:
        return True                                   # 纯 role：角色对上就算
    return bool(name) and (name.lower() in tname.lower() or tname.lower() in name.lower())


def report(app: str, n: int) -> None:
    app_dir = BENCH / app
    tree, _ = load_requirement_tree(app_dir)
    a11y = extract_accessible_names(tree)
    prose = [e for e in a11y.entries if (e.pattern or "").startswith("prose")]
    hard = [e for e in a11y.entries if not (e.pattern or "").startswith("prose")]

    print("=" * 86)
    print(f"{app}：靶子 {len(a11y.entries)} 条 = 引号/规则式 {len(hard)} + **散文 {len(prose)}**")
    print("=" * 86)

    targets = _test_targets(app_dir)
    hit = 0
    missed: list[tuple[str, str]] = []
    for t in sorted(targets):
        if any(_matches(e.role, e.name, t) for e in prose) or \
           any(_matches(e.role, e.name, t) for e in hard):
            hit += 1
        else:
            missed.append(t)
    print(f"判据里的 (role,name) 目标 {len(targets)} 个；被靶子清单覆盖 {hit} 个"
          f" = **召回 {hit / max(1, len(targets)):.0%}**（测量用，不是抽取依据）")
    if missed:
        print(f"  未覆盖（{len(missed)}）：")
        for role, name in missed[:10]:
            print(f"    - role={role!r} name={name!r}")

    print(f"\n--- 精度样本（前 {n} 条散文靶子，人工判「像不像可交互对象」）---")
    for i, e in enumerate(prose[:n], 1):
        nm = e.name or "(纯 role，无名字)"
        print(f"  {i:2d}. [{(e.role or '?')}] {nm!r}")
        print(f"      evidence: {e.evidence[:96]!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app", nargs="?", default="keep")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    todo = APPS if a.all else [a.app]
    for app in todo:
        if (BENCH / app).is_dir():
            report(app, a.n)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
