"""模型客户端（管线第一个会花钱的模块）。

**为什么直接写 HTTP 而不用 `openai` 包**：
  1. `PLAN.md` §4.5 要求"计量先行"——必须逐次调用记 `usage`。自己发请求才能保证不漏记；
  2. 少一个重依赖（`openai` 也在 `requirements.txt` 里，但那是"只增不减"的上游条目，不必用）；
  3. 网关是 OpenAI 兼容的 `/chat/completions`，直接用 `requests` 即可。

**配置来源**（按优先级，兼容平台注入与本地 `.env`）：
  key    : `OPENAI_API_KEY` / `ARC_API_KEY` / `ARC_OPENAI_API_KEY`
  base   : `OPENAI_BASE_URL` / `ARC_BASE_URL`（默认 `https://api.arc-bench.com/v1`）
  model  : `MODEL` / `ARC_MODEL`（平台会注入 `MODEL`）

**计量落盘**：每次调用追加一行到 `<output_dir>/.arc/metrics.jsonl`：
  `{ts, stage, node_id, model, input_tokens, output_tokens, total_tokens, elapsed_s}`
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE_URL = "https://api.arc-bench.com/v1"
DEFAULT_MODEL = "deepseek-v4-flash"

# 本次进程的 run 标识：让 metrics.jsonl 的每一行都能归到一次 run。
# 为什么需要（2026-09-21 的一次数错）：一次 run 里有**两轮** selfcheck→repair，
# 而记录里没有轮次标识，抄表的人只抄到前半段就得到中间态（131,360 而不是 156,343）。
# `call_index` 给出无歧义的顺序，`run_id` 给出归属；可用 PIPELINE_RUN_ID 显式覆盖。
RUN_ID = (os.environ.get("PIPELINE_RUN_ID") or "").strip() or (
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-p" + str(os.getpid()))


def _global_extra() -> dict:
    """全局请求参数（M3b-1 标定/生产都用它）：

        PIPELINE_LLM_EXTRA='{"reasoning_effort": "low"}'

    实测（2026-09-20 标定）：网关**接受** `reasoning_effort`，
    在 deepseek-v4-flash 上把 reasoning 从 949 压到 52、成本指数 −60%，
    而**正文没有变短**（三个配置的正文长度 856 / 1269 / 1125 字符）。
    """
    raw = (os.environ.get("PIPELINE_LLM_EXTRA") or "").strip()
    if not raw:
        return {}
    try:
        got = json.loads(raw)
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001 —— 写错了就当没设，不要静默改行为
        return {}


class RequestTooLarge(RuntimeError):
    """单次请求超出输入预算 —— 调用方应当**分块**，而不是重发同一个大请求。

    为什么要有这个类型（2026-09-21 立的客户端规则）：「大请求被网关丢」已经出现三次
    （M3a 后端块、M3a design 的输出全被 reasoning 吃掉、M3b-1 的 selfcheck-review
    连续 `RemoteDisconnected` ×2）。所以不再逐点打补丁，改成**预算 + 分块**：
    超限或"偏大 + 被断连"都抛这个，由调用方按语义切分。
    """


class LLMError(RuntimeError):
    """调用模型失败——必须报出来，不能静默降级成"生成成功但什么都没有"。"""


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout_s: int = 300
    metrics_path: Path | None = None
    calls: list[dict] = field(default_factory=list)

    @classmethod
    def from_env(cls, output_dir: Path | None = None, model: str | None = None) -> "LLMConfig":
        def first(*names: str) -> str:
            for n in names:
                v = (os.environ.get(n) or "").strip()
                if v:
                    return v
            return ""

        # 本地测试可以用 PIPELINE_LLM_* 显式指向别家（例如 DeepSeek 官方），
        # 这样就不必烧比赛网关的额度。平台运行时不设这些变量 → 仍走平台注入的 key。
        # 顺序：显式覆盖（PIPELINE_LLM_*）→ 平台注入的变量 → 工作区 .env 兜底
        key = first("PIPELINE_LLM_API_KEY", "OPENAI_API_KEY", "ARC_API_KEY", "ARC_OPENAI_API_KEY")
        if not key:
            key = (_from_dotenv("PIPELINE_LLM_API_KEY")
                   or _from_dotenv("OPENAI_API_KEY") or _from_dotenv("ARC_API_KEY"))
        base = (first("PIPELINE_LLM_BASE_URL", "OPENAI_BASE_URL", "ARC_BASE_URL")
                or _from_dotenv("PIPELINE_LLM_BASE_URL") or _from_dotenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL)
        mdl = (model or first("PIPELINE_LLM_MODEL", "MODEL", "ARC_MODEL")
               or _from_dotenv("PIPELINE_LLM_MODEL") or _from_dotenv("MODEL") or DEFAULT_MODEL)
        metrics = (output_dir / ".arc" / "metrics.jsonl") if output_dir else None
        return cls(api_key=key, base_url=base.rstrip("/"), model=mdl, metrics_path=metrics)

    def require_key(self) -> None:
        if not self.api_key:
            raise LLMError(
                "找不到 API key：环境变量 OPENAI_API_KEY / ARC_API_KEY 都没有，"
                "工作区根的 .env 里也没有。"
            )


def _from_dotenv(name: str) -> str:
    """只从**工作区根**的 .env 取一个变量——本地开发用，平台运行时不依赖它。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / ".env"
        if cand.is_file():
            try:
                for line in cand.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == name:
                        return v.strip().strip('"').strip("'")
            except Exception:  # noqa: BLE001 —— 读不到就当没有
                return ""
    return ""


# ---- 单次请求的输入预算（2026-09-21 立的客户端规则）----
# 「大请求被网关丢」已经出现三次：M3a 的后端块、M3a 的 design（输出全被 reasoning 吃掉）、
# 本轮 M3b-1 的 selfcheck-review（连续 RemoteDisconnected ×2）。
# → 不再逐点打补丁，改成**预算 + 分块**：超限就报 `RequestTooLarge`，由调用方按语义切分。
MAX_REQUEST_CHARS = int((os.environ.get("PIPELINE_LLM_MAX_REQUEST_CHARS") or "24000").strip() or 24000)


def estimate_request_chars(messages: list[dict]) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def chat(cfg: LLMConfig, messages: list[dict], *, stage: str, node_id: str = "",
         temperature: float = 0.2, max_tokens: int | None = None,
         extra: dict | None = None,
         attempts: int = 3, log=print) -> str:
    """发一次 chat/completions，返回助手文本；usage 落盘。

    **带重试**：实测网关会对长生成断开连接（`RemoteDisconnected`，无响应）。
    重试只针对**连接层失败与 5xx/429**——校验类错误（4xx）重试没有意义，直接抛。
    """
    import requests  # 延迟 import：只有真要调用时才需要

    cfg.require_key()
    size = estimate_request_chars(messages)
    if size > MAX_REQUEST_CHARS:
        raise RequestTooLarge(
            f"请求输入 {size} 字符 > 预算 {MAX_REQUEST_CHARS}（PIPELINE_LLM_MAX_REQUEST_CHARS 可调）"
            f"：请按语义**分块**（stage={stage}），不要重发同一个大请求"
        )
    url = f"{cfg.base_url}/chat/completions"
    payload: dict = {"model": cfg.model, "messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    # 额外参数口：M3b-1 标定要试 reasoning_effort 一类旋钮，不该为它改签名
    merged = dict(_global_extra())
    if extra:
        merged.update(extra)
    if merged:
        payload.update(merged)

    last_err: str = ""
    for attempt in range(1, attempts + 1):
        started = time.time()
        try:
            resp = requests.post(
                url, json=payload, timeout=cfg.timeout_s,
                headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
            )
        except Exception as exc:  # noqa: BLE001 —— 连接层失败
            last_err = f"连接失败：{exc!r}"
            # 请求**接近预算** + 被断连 = 多半是网关丢大请求，重发同一个没有意义，
            # 抛"太大"让调用方分块。
            # ⚠️ 阈值从 `MAX_REQUEST_CHARS // 2`（12,000）收紧到 0.8×预算，原因是实测：
            # 闸门第 0 条第三次跑时，**12,974 字符**（54% 预算）的 backend-auth 也被断连，
            # 老阈值把它判成"太大" → **直接抛出去、整个 run 作废**。
            # 而 13k 字符的请求只是中等大小，重试才是对的（连接层抖动比"真太大"常见得多）。
            if "RemoteDisconnected" in repr(exc) and size > int(MAX_REQUEST_CHARS * 0.8):
                raise RequestTooLarge(
                    f"网关断开且请求接近预算（{size} 字符 ≈ {size / MAX_REQUEST_CHARS:.0%}，"
                    f"stage={stage}）：**应当分块**再试"
                ) from exc
            resp = None
        else:
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}：{resp.text[:200]}"
            elif resp.status_code != 200:
                raise LLMError(f"{url} 返回 {resp.status_code}（不重试）：{resp.text[:400]}")
            else:
                try:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                except Exception as exc:  # noqa: BLE001
                    raise LLMError(
                        f"响应结构不认识：{exc!r} 原文前 400 字：{resp.text[:400]}"
                    ) from exc
                # ⚠️ **先记用量再看内容**：空内容的调用前几天**没有**被记账（metrics.jsonl 里
                # 直接少一条），于是"这一次 run 花了多少"永远对不上。
                rec = _record(cfg, data.get("usage") or {}, stage=stage, node_id=node_id,
                              started=started, empty=not (content or "").strip())
                if (content or "").strip():
                    return content
                # 空内容 = **整段输出被 reasoning 吃掉**。实测（2026-09-21 闸门第 0 条那次）：
                # 同一 run 的 design 用掉 83% 输出，紧接的 schema 块直接返回空 → 生成失败、
                # 连产物都没有（平台侧就是 0 分 + "Run failed"）。
                # 这是**可重试**的：同一个请求换个采样就能返回正文。所以走重试，不要立刻抛。
                last_err = (f"空内容（输出 {rec.get('output_tokens')} token，"
                            f"其中 reasoning {rec.get('reasoning_tokens')}）——整段被 reasoning 吃掉")

        if attempt < attempts:
            wait = 5 * attempt
            log(f"  ⚠️  {stage} 第 {attempt}/{attempts} 次失败（{last_err}），{wait}s 后重试")
            time.sleep(wait)
    raise LLMError(f"{stage} 连续 {attempts} 次失败，最后一次：{last_err}")


def _record(cfg: LLMConfig, usage: dict, *, stage: str, node_id: str, started: float,
            empty: bool = False) -> dict:
    """记一次调用的用量（**成功与失败都要记**），返回记录本身。"""
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": RUN_ID,
        "call_index": len(cfg.calls) + 1,
        "stage": stage,
        "node_id": node_id,
        "model": cfg.model,
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        # 网关计费**包含 reasoning token**（PLAN §11 V4 实测），把它单独记下来
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "elapsed_s": round(time.time() - started, 1),
    }
    if empty:
        record["empty_content"] = True
    cfg.calls.append(record)
    if cfg.metrics_path:
        cfg.metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg.metrics_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def totals(cfg: LLMConfig) -> dict:
    """本次进程内的累计用量（写成汇总，方便和 `.arc/metrics.jsonl` 对账）。"""
    inp = sum(c["input_tokens"] or 0 for c in cfg.calls)
    out = sum(c["output_tokens"] or 0 for c in cfg.calls)
    return {"calls": len(cfg.calls), "input_tokens": inp, "output_tokens": out,
            "total_tokens": inp + out}
