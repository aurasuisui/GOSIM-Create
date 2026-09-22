#!/usr/bin/env python3
"""红线 9 的自查：提交包里**不许出现题目特定字符串**（`AGENTS.md` 硬规则 9）。

**为什么要机器查**（本项目的一贯结论：**靠纪律防不住，得靠机器**）：
红线 9 的原文是"模板里零业务语义；全仓不能出现题目特定字符串"，而**提交包的作用域
= `pipeline/` + `templates/`**（`docs/` 不提交，可以且应该继续用样本名做实测记录）。
2026-09-22 第二十七轮审核在包里数出 19 处（4 个文件）——是**人眼+一次性的脚本**发现的，
所以下一步就是把它变成常驻判据：**每次打包前跑一次**（`eval/package.sh` 里已接线）。

**判据（两条，都要过）**
  1. 五个非 `keep` 的 app 名，一次都不许出现（它们只可能是题目引用）；
  2. `keep` 是英文常用词，**不能一律禁**（`agentkeepalive` 依赖、`keep fixtures minimal` 这类注释）——
     所以只禁**题目引用形态**：`webapp/keep`、`keep 实测/那轮/的/全量/子集/需求`、`'keep'`/`"keep"`、
     `keep: <数字>`、`m2-keep`/`e1b-keep` 这类 run 名。

用法：
    python eval/check_bundle_strings.py            # 查 output/bundle.zip（默认）
    python eval/check_bundle_strings.py --dir pipeline templates   # 查磁盘目录（打包前自检）
退出码：0 = 干净；1 = 有命中（逐条打印 文件:行号:原文）
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ① 非 keep 的 app 名：出现即违规
RE_FIVE = re.compile(r"prestashop|ctrip|12306|bookstack|stackoverflow", re.I)
# ② `keep` 的**题目引用形态**（英文词放行）
RE_KEEP_REF = re.compile(
    r"(webapp/keep|keep\s*(?:实测|那轮|全量|子集|需求|的)|keep\s*REQ-|['\"]keep['\"]|keep\s*:\s*\d|"
    r"m2-keep|e1b-keep|e-keep|e2-keep|r3b|r4-keep|keep-score)", re.I)
TEXT_EXT = {".py", ".js", ".ts", ".tsx", ".json", ".md", ".txt", ".sh", ".yml", ".yaml"}
SKIP_NAMES = {"package-lock.json"}          # 依赖锁文件里必然有 agentkeepalive 这类名字


def scan_text(name: str, text: str) -> list[tuple[str, int, str]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if RE_FIVE.search(line) or RE_KEEP_REF.search(line):
            out.append((name, i, line.strip()[:110]))
    return out


def scan_zip(path: Path) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.endswith("/") or Path(n).name in SKIP_NAMES:
                continue
            if Path(n).suffix.lower() not in TEXT_EXT:
                continue
            out += scan_text(n, z.read(n).decode("utf-8", errors="replace"))
    return out


def scan_dirs(dirs: list[str]) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for d in dirs:
        base = ROOT / d
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name in SKIP_NAMES or p.suffix.lower() not in TEXT_EXT:
                continue
            out += scan_text(str(p.relative_to(ROOT)).replace("\\", "/"),
                             p.read_text(encoding="utf-8", errors="replace"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="自查提交包里的题目特定字符串（红线 9）")
    ap.add_argument("--zip", default="output/bundle.zip")
    ap.add_argument("--dir", nargs="*", default=None, help="改查磁盘目录（如 pipeline templates）")
    args = ap.parse_args()

    if args.dir:
        target = " + ".join(args.dir)
        hits = scan_dirs(args.dir)
    else:
        # 相对路径按仓库根解析；绝对路径（自检/变异测试用）直接用
        z = Path(args.zip)
        if not z.is_absolute():
            z = ROOT / z
        if not z.is_file():
            print(f"找不到 {z}（先跑 eval/package.sh）")
            return 2
        target = str(z)
        hits = scan_zip(z)

    print(f"=== 红线 9 自查：{target} ===")
    print("    判据：① 五个非 keep 的 app 名一次都不许出现；② keep 只禁**题目引用形态**"
          "（`webapp/keep` / `keep 实测` / `'keep'` / run 名…；英文词与依赖名放行）")
    if not hits:
        print("    ✅ 干净（0 处命中）")
        return 0
    print(f"    ❌ {len(hits)} 处命中：")
    for name, line_no, line in hits:
        print(f"       {name}:{line_no}  {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
