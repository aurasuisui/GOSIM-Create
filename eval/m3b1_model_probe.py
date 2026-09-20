#!/usr/bin/env python3
"""M3b-1 前置：模型 / 成本口径标定 —— 第一段「便宜筛选」。

**为什么这么设计**：`PLAN.md` §7 说三个旋钮每个候选"约 10 万 token 一次"（整跑）。
但**换模型族的差异在小调用上就能看出来**（reasoning 占比是模型性质，不依赖任务大小）。
所以先用**同一个代表性小任务**（写一个含校验的 Express 路由文件，约 40 行）
筛掉明显更贵的模型，只对胜出者做完整端到端确认。

**度量口径（PLAN.md §7 明文要求）**：`token / 通过条数`，不是 token 绝对值——
否则"省 token 换掉通过率"会被记成收益。本段只量 token 与 reasoning 占比；
通过条数在第二段（端到端）才拿得到。

用法：python eval/m3b1_model_probe.py            # 用工作区 .env 里的网关 key
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from generate.llm import LLMConfig, chat, totals  # noqa: E402

# 同一个代表性任务：与生成阶段"一次产出一个含校验的路由文件"同构
TASK = """只输出一个代码块：写一个 Express 路由文件（约 40 行），
含 POST /api/items 的完整服务端校验（必填、字符集、长度、重复冲突），
错误用中文消息，成功返回 201。不要解释。"""

CANDIDATES = [
    ("deepseek-v4-flash", None),
    ("glm-5.3-flash", None),
    ("kimi-k2.7-code-highspeed", None),
    ("deepseek-v4-flash", {"reasoning_effort": "low"}),
    ("deepseek-v4-flash", {"reasoning_effort": "minimal"}),
    ("glm-5.3-flash", {"reasoning_effort": "low"}),
]


def gateway_config(model: str) -> LLMConfig:
    """显式走**比赛网关**（不用 .env 里指向 DeepSeek 官方的 PIPELINE_LLM_*）——
    标定的目的就是给平台上跑的模型选型，所以必须量网关这一侧。"""
    from generate.llm import _from_dotenv
    key = _from_dotenv("OPENAI_API_KEY") or _from_dotenv("ARC_API_KEY")
    base = _from_dotenv("OPENAI_BASE_URL") or "https://api.arc-bench.com/v1"
    return LLMConfig(api_key=key, base_url=base.rstrip("/"), model=model)


def main() -> int:
    print("=" * 92)
    print("M3b-1 标定 · 第一段：模型族与 reasoning 占比（同一小任务，逐候选）")
    print("=" * 92)
    print(f"{'模型':30s} {'extra':26s} {'in':>6s} {'out':>7s} {'reasoning':>10s} {'占比':>6s} {'秒':>5s}")
    print("-" * 92)
    rows = []
    for model, extra in CANDIDATES:
        cfg = gateway_config(model)
        if not cfg.api_key:
            print("❌ 拿不到网关 key（.env 里 OPENAI_API_KEY / ARC_API_KEY 都为空）")
            return 1
        t0 = time.time()
        try:
            out = chat(cfg, [{"role": "user", "content": TASK}],
                       stage=f"probe-{model}", extra=extra, attempts=2, log=lambda *_: None)
        except Exception as exc:  # noqa: BLE001
            print(f"{model:30s} {str(extra):26s} ❌ {str(exc)[:40]}")
            rows.append({"model": model, "extra": extra, "error": str(exc)[:200]})
            continue
        u = cfg.calls[-1]
        reason = u.get("reasoning_tokens")
        share = (reason / u["output_tokens"]) if (reason and u["output_tokens"]) else None
        print(f"{model:30s} {str(extra):26s} {u['input_tokens']:6} {u['output_tokens']:7} "
              f"{str(reason):>10s} {(f'{share*100:.0f}%' if share is not None else '—'):>6s} "
              f"{time.time()-t0:5.1f}")
        rows.append({"model": model, "extra": extra, "usage": u,
                     "code_chars": len(out), "reasoning_share": share})
    out_path = ROOT / "runs" / "20260920T-m3b1-model-probe.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"明细 → {out_path.relative_to(ROOT)}")
    print()
    print("判读提示：")
    print("  · reasoning 占比低 + 输出长度正常 → 该模型适合铺开（M3b-1）")
    print("  · `reasoning_effort` 若被接受，应看到 reasoning 下降且正文没被截断")
    print("  · 通过条数这一栏要等第二段（端到端）——按 PLAN §7，口径是 token/通过条数")
    return 0


if __name__ == "__main__":
    sys.exit(main())
