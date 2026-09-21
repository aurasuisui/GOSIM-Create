#!/usr/bin/env python3
"""CLI：对给定文件/目录做 JS/TSX 词法级配平扫描（实现见 `pipeline/verify/jsscan.py`）。

存在理由：run e 的产物因为 `});` 少写一个 `)` 导致后端起不来（23.4 万 token 归零），
而当时既没有这个 CLI、L1 也不看语法。现在线上（L1）与线下（本脚本）共用同一份实现。

用法：
    python eval/check_js_balance.py <文件或目录> [更多…]
    python eval/check_js_balance.py "%TEMP%/m3b1-gate0e/app"

退出码：全配平 0，有问题的文件 1（便于进 CI / 手工链）
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from verify.jsscan import scan  # noqa: E402

EXTS = (".js", ".jsx", ".ts", ".tsx")


def main() -> int:
    targets: list[pathlib.Path] = []
    for a in sys.argv[1:]:
        p = pathlib.Path(a)
        if p.is_dir():
            targets += [f for f in sorted(p.rglob("*")) if f.is_file() and f.suffix in EXTS]
        elif p.is_file():
            targets.append(p)
    if not targets:
        print("用法：python eval/check_js_balance.py <文件或目录> …", file=sys.stderr)
        return 2
    bad = 0
    for f in targets:
        got = scan(f.read_text(encoding="utf-8", errors="replace"))
        if got:
            bad += 1
            print(f"❌ {f}")
            for g in got[:6]:
                print(f"     {g}")
    print(f"扫描 {len(targets)} 个文件，问题 {bad} 个")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
