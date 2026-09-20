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
        except Exception as exc:  # noqa: BLE001 —— 连接层失败：可重试
            last_err = f"连接失败：{exc!r}"
            resp = None
        else:
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}：{resp.text[:200]}"
            elif resp.status_code != 200:
                raise LLMError(f"{url} 返回 {resp.status_code}（不重试）：{resp.text[:400]}")
            else:
                break  # 成功

        if attempt < attempts:
            wait = 5 * attempt
            log(f"  ⚠️  {stage} 第 {attempt}/{attempts} 次失败（{last_err}），{wait}s 后重试")
            time.sleep(wait)
    else:
        raise LLMError(f"{stage} 连续 {attempts} 次失败，最后一次：{last_err}")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"响应结构不认识：{exc!r} 原文前 400 字：{resp.text[:400]}") from exc
    if not (content or "").strip():
        raise LLMError(f"{stage} 返回了空内容（可能整段输出都被 reasoning 吃掉或截断）")

    usage = data.get("usage") or {}
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    cfg.calls.append(record)
    if cfg.metrics_path:
        cfg.metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg.metrics_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return content


def totals(cfg: LLMConfig) -> dict:
    """本次进程内的累计用量（写成汇总，方便和 `.arc/metrics.jsonl` 对账）。"""
    inp = sum(c["input_tokens"] or 0 for c in cfg.calls)
    out = sum(c["output_tokens"] or 0 for c in cfg.calls)
    return {"calls": len(cfg.calls), "input_tokens": inp, "output_tokens": out,
            "total_tokens": inp + out}
