"""本地基线：把模板落成一份"空应用"，量出判分链路的底噪。

目的：在写任何生成代码之前，先证明"能起服务、能跑官方测试、能出读数"。
预期：0/34 —— 因为模板零业务语义。任何非零都说明测试根本没跑起来，要先查链路。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "g2"))

from app.gen.scaffold import scaffold_app  # noqa: E402

out = ROOT / "g2/work" / "baseline"
out.mkdir(parents=True, exist_ok=True)
info = scaffold_app(out)
print("scaffold:", info)