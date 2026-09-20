"""YAML 缩进修复。

## 为什么需要这个模块

2026-09-20 实测：**六个赛题里有 2 个的需求文件不是合法 YAML。**

    bookstack  第 603 行、第 645 行 —— mapping values are not allowed here
    keep       第 445 行           —— mapping values are not allowed here

三处都是同一种毛病：**一个键比它的同级兄弟多缩进 4 格。**

    bookstack:603
        602|              - keyword: GIVEN        ← dash 在 14，键应在 16
        603|                    content: ...       ← 实际在 20，多 4 格
    keep:445
        444|        type: ATOMIC                   ← 同级键在 8
        445|            description: '...'          ← 实际在 12，多 4 格

## 为什么这件事是致命的

1. 平台主赛道的计分前提是**同一份提交跑完全部 6 个任务**
   （`PLAN.md` §1，原文：*A complete leaderboard score is calculated only
   when one submission has a completed run for every task*）。
2. 上游 ARC 的加载器是 `yaml.safe_load`（`core/files.py:11-23`），
   **没有容错层**——它会在这两个文件上直接抛 `ScannerError`。
3. 于是"原样打包 ARC"这条保底路线，在这两个任务上是拿不到分的。

**两个任务直接不可编译 = 整条榜单分不成立。** 9/19 实测该赛道
Current leader 仍是 `No data yet`，与此吻合。

## 修复策略

不做手写的缩进猜测（那种启发式在嵌套场景下必然误伤）。改用
**解析器驱动的搜索**，只信 YAML 自己的判断：

1. 解析失败 → 拿到错误行号
2. 该行是个 `key:` 行 → 从"附近真实出现过的缩进值"里挑候选
3. 逐个试：**只要让错误位置严格后退，或让整份文件通过，就接受**
4. 不接受任何"没有让错误前移"的改动 —— 保证每次修复都是净进展
5. 改不动就带着诊断信息放弃，绝不产出半修好的树

这样每一步都可验证：修复后要么解析通过，要么错误位置单调后移。
上限 200 轮兜底。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# `key:` 形态（缩进 + 键名 + 冒号）
KEY_LINE = __import__("re").compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*):(\s|$)")

# `- key:` 形态：序列项里的映射，键的有效缩进 = dash 缩进 + 2
SEQ_KEY_LINE = __import__("re").compile(r"^(\s*)-\s+([A-Za-z_][A-Za-z0-9_]*):")

# 附近缩进采样的半径
WINDOW = 40
MAX_ROUNDS = 200


@dataclass
class Repair:
    """一处修复，留痕用。"""

    line: int          # 1-based
    old_indent: int
    new_indent: int
    key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "key": self.key,
            "old_indent": self.old_indent,
            "new_indent": self.new_indent,
        }


@dataclass
class RepairResult:
    text: str | None
    repairs: list[Repair] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.text is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "repair_count": len(self.repairs),
            "repairs": [r.to_dict() for r in self.repairs],
            "error": self.error,
        }


def _try_parse(lines: list[str]) -> yaml.YAMLError | None:
    """返回 None 表示解析成功，否则返回 YAMLError。"""
    try:
        yaml.safe_load("\n".join(lines))
        return None
    except yaml.YAMLError as exc:
        return exc


def _plausible_indents(lines: list[str], idx: int) -> list[int]:
    """收集附近真实出现过的缩进值，作为候选。

    不从 0 开始穷举，是为了让候选落在"这份文件自己的排版习惯"里，
    而不是把某一行推到某个语义上说不通的位置。
    """
    lo = max(0, idx - WINDOW)
    hi = min(len(lines), idx + WINDOW)
    out: set[int] = {0}
    for i in range(lo, hi):
        if i == idx:
            continue
        m = KEY_LINE.match(lines[i])
        if m:
            out.add(len(m.group(1)))
        sm = SEQ_KEY_LINE.match(lines[i])
        if sm:
            # 序列项内映射的第一个键：dash 缩进 + 2
            out.add(len(sm.group(1)) + 2)
    return sorted(out)


def repair_indentation(text: str) -> RepairResult:
    """把因缩进错误而无法解析的 YAML 修到能解析。

    已经能解析的文本原样返回（repairs 为空）。
    """
    lines = text.split("\n")
    repairs: list[Repair] = []

    err = _try_parse(lines)
    if err is None:
        return RepairResult(text=text, repairs=[])

    for _ in range(MAX_ROUNDS):
        err = _try_parse(lines)
        if err is None:
            return RepairResult(text="\n".join(lines), repairs=repairs)

        mark = getattr(err, "problem_mark", None)
        if mark is None:
            return RepairResult(
                text=None, repairs=repairs,
                error=f"YAML 报了错但没给位置：{err}",
            )

        idx = mark.line
        if idx >= len(lines):
            return RepairResult(text=None, repairs=repairs, error="错误行号越界")

        m = KEY_LINE.match(lines[idx])
        if not m:
            return RepairResult(
                text=None, repairs=repairs,
                error=f"第 {idx + 1} 行不是 `key:` 形态，无法用本策略修复："
                      f"{lines[idx].strip()[:80]!r}",
            )

        key = m.group(2)
        cur = len(m.group(1))
        original = lines[idx]

        # 候选按"离当前缩进最近"排序：最小改动优先
        progressed = False
        for cand in sorted(_plausible_indents(lines, idx), key=lambda c: abs(c - cur)):
            if cand == cur:
                continue
            lines[idx] = " " * cand + original.lstrip()
            err2 = _try_parse(lines)
            if err2 is None or (
                getattr(err2, "problem_mark", None)
                and err2.problem_mark.line > idx
            ):
                repairs.append(Repair(line=idx + 1, old_indent=cur,
                                      new_indent=cand, key=key))
                progressed = True
                break
            lines[idx] = original  # 候选没用，回滚

        if not progressed:
            return RepairResult(
                text=None, repairs=repairs,
                error=f"第 {idx + 1} 行（键 {key!r}）没有可用缩进——"
                      "附近的候选都推不动错误位置。需要人工看。",
            )

    return RepairResult(text=None, repairs=repairs, error=f"超过 {MAX_ROUNDS} 轮仍未修好")


def load_yaml_lenient(path: str | Path) -> tuple[Any, RepairResult]:
    """读取 YAML，必要时先修缩进。

    返回 (解析结果, 修复报告)。**修复报告必须落盘留痕**——
    我们改了输入，这在赛后复核时说得清。
    """
    p = Path(path)
    raw_text = p.read_text(encoding="utf-8")

    try:
        return yaml.safe_load(raw_text), RepairResult(text=raw_text, repairs=[])
    except yaml.YAMLError:
        pass

    result = repair_indentation(raw_text)
    if not result.ok:
        raise ValueError(f"{p}: YAML 解析失败且修复未成功 —— {result.error}")
    return yaml.safe_load(result.text), result
