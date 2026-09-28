"""对**已有产物**跑确定性改写：模板字符串 → 拼接。

用法：python g2/tools/defix.py <产物目录> [更多目录...]
为什么单独做成工具：一次 emit 写几十个文件，而修复轮/多轮迭代会引入新的模板串；
留一个可以随时重跑的入口，比只在写入时改更可靠。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "g2")
from app.gen.defix import convert_templates  # noqa: E402


def main(argv: list[str]) -> int:
    total = 0
    for arg in argv[1:] or ["g2/work"]:
        root = Path(arg)
        for p in list(root.rglob("*.tsx")) + list(root.rglob("*.ts")) + list(root.rglob("*.js")):
            if "node_modules" in p.parts or "dist" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if "`" not in text:
                continue
            new, n = convert_templates(text)
            if n:
                p.write_text(new, encoding="utf-8")
                total += n
                print(f"  {p}: {n} 处")
    print(f"共改写 {total} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))