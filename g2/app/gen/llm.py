"""模型客户端：**预算先于发送**。

旧管线的死法（2026-09-26 平台实测）：design 那一次请求把整份 brief 塞进去，
全量需求下这条请求 27k–121k 字符 > 24000 预算 → 抛 RequestTooLarge → 整轮 run 死 → 0 分。
所以这里的规则是硬的：

  1. **任何一次请求都要先过 `fit()`**——它按"截断优先级"就地降级，绝不抛异常让整轮死；
  2. 每次调用都往 `<out>/.arc/metrics.jsonl` 落一行（含 endpoint，因为网关与官方口径差十倍）；
  3. `chat()` 只接受已经过 `fit()` 的消息（超预算直接抛，这是代码 bug，不是运行期意外）。

配置来源（兼容平台注入与本地 .env，按优先级）
  key   : PIPELINE_LLM_API_KEY / ARC_API_KEY / OPENAI_API_KEY
  base  : PIPELINE_LLM_BASE_URL / ARC_BASE_URL / OPENAI_BASE_URL（默认 https://api.arc-bench.com/v1）
  model : MODEL / ARC_MODEL / PIPELINE_LLM_MODEL（平台会注入 MODEL）
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

# 单次请求的输入字符预算。平台侧实测的"网关丢大请求"档位在 80% 附近出现过，
# 所以这里取一个**留有余量**的值：20000（≈ 旧管线 24000 的 83%）。
REQUEST_BUDGET = int((os.environ.get("G2_LLM_MAX_REQUEST_CHARS") or "20000").strip() or 20000)
# 输出上限。**必须给够 reasoning 的空间**：实测网关计费包含 reasoning token，
# 而 reasoning 能占输出的 86–93% → 给太小就会出现"返回内容为空"（reasoning 吃光了额度），
# 旧管线在这里栽过（`docs/12`：一轮 3 次调用 2 次空）。
# 一文件一调用 ≈ 6k 字符 ≈ 2k token 正文，给 12000 留 6 倍余量。
# 12000 → 20000：实测官方题（页面更长）约 **11% 的文件**会撞到上限 →
# 触发"精简重问"，而精简版是**被缩水的页面**（判据要的功能可能就没了）。
# 实测网关接受 16000/32000（都返回 200），所以这上调不花额外钱：
# max_tokens 只是上限，**按实际输出计费**。
MAX_OUTPUT_TOKENS = int((os.environ.get("G2_LLM_MAX_OUTPUT_TOKENS") or "20000").strip() or 20000)
# 结构设计要一次吐 routes+endpoints+tables 的 JSON，正文更长 → 单独给更大的额度。
DESIGN_OUTPUT_TOKENS = int((os.environ.get("G2_LLM_DESIGN_OUTPUT_TOKENS") or "24000").strip() or 24000)

# reasoning 旋钮。**为什么要留这个口**（实测口径，`docs/12`）：
#   网关计费含 reasoning token，而 reasoning 占输出 86–93% → 一次"只回几个字"的调用
#   也可能烧掉上百 token；而写作阶段我们**不需要**那么多思考：文件内容由需求与结构决定。
#   ⚠️ 但实测过"压低 reasoning 会掉通过率" → 所以**默认不设**，由环境变量显式开启，
#   并且只在写作阶段生效（设计阶段保持默认强度）。
REASONING_EFFORT = (os.environ.get("G2_REASONING_EFFORT") or "").strip()

RUN_ID = (os.environ.get("G2_RUN_ID") or "").strip() or (
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-p" + str(os.getpid()))


def _env_first(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v.strip('"').strip("'")
    return ""


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_s: int = 600
    warm: bool = False

    @classmethod
    def from_env(cls, *, output_dir: Path | None = None) -> "LLMConfig":
        key = _env_first("PIPELINE_LLM_API_KEY", "ARC_API_KEY", "OPENAI_API_KEY", "ARC_OPENAI_API_KEY")
        base = _env_first("PIPELINE_LLM_BASE_URL", "ARC_BASE_URL", "OPENAI_BASE_URL") or DEFAULT_BASE_URL
        model = _env_first("MODEL", "ARC_MODEL", "PIPELINE_LLM_MODEL") or DEFAULT_MODEL
        if not key:
            # 本地开发：允许从工作区 .env 读（平台则直接注入环境变量）。
            # 起点默认取 cwd —— 平台容器里没有 .env，这一步自然落空。
            start = Path(output_dir) if output_dir is not None else Path.cwd()
            key = _key_from_env_file(start, "ARC_API_KEY", "PIPELINE_LLM_API_KEY",
                                     "OPENAI_API_KEY", "ARC_OPENAI_API_KEY")
        return cls(api_key=key, base_url=base, model=model)

    def require_key(self) -> None:
        if not self.api_key:
            raise LLMError("没有模型凭据：设 ARC_API_KEY / PIPELINE_LLM_API_KEY，或让平台注入")

    def masked(self) -> dict:
        return {"base_url": self.base_url, "model": self.model,
                "key": f"set(len={len(self.api_key)})" if self.api_key else "absent"}


def _key_from_env_file(start: Path, *names: str) -> str:
    for d in [start, *start.parents]:
        f = d / ".env"
        if not f.is_file():
            continue
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() in names:
                    return v.strip().strip('"').strip("'")
        except OSError:
            return ""
    return ""


class LLMError(RuntimeError):
    pass


@dataclass
class CallRecord:
    stage: str
    node_id: str = ""
    model: str = ""
    endpoint: str = ""
    input_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    elapsed_s: float = 0.0
    attempt: int = 1
    ok: bool = True
    error: str = ""
    call_index: int = 0
    extra: dict = field(default_factory=dict)


class Ledger:
    """逐次调用记账。三条口径都要能算出来（含失败尝试）。"""

    def __init__(self, output_dir: Path | None = None) -> None:
        self.calls: list[CallRecord] = []
        self.output_dir = Path(output_dir) if output_dir else None

    def add(self, rec: CallRecord) -> None:
        rec.call_index = len(self.calls) + 1
        self.calls.append(rec)
        if self.output_dir is not None:
            p = self.output_dir / ".arc" / "metrics.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "run_id": RUN_ID, "call_index": rec.call_index, "stage": rec.stage,
                    "node_id": rec.node_id, "model": rec.model, "endpoint": rec.endpoint,
                    "input_chars": rec.input_chars, "input_tokens": rec.input_tokens,
                    "output_tokens": rec.output_tokens, "reasoning_tokens": rec.reasoning_tokens,
                    "total_tokens": rec.total_tokens, "elapsed_s": round(rec.elapsed_s, 2),
                    "attempt": rec.attempt, "ok": rec.ok, "error": rec.error[:200],
                    **rec.extra,
                }, ensure_ascii=False) + "\n")

    def totals(self) -> dict:
        ok = [c for c in self.calls if c.ok]
        return {
            "calls": len(self.calls),
            "calls_ok": len(ok),
            "tokens_ok": sum(c.total_tokens for c in ok),
            "tokens_all": sum(c.total_tokens for c in self.calls),
            "chars_all": sum(c.input_chars for c in self.calls),
            "elapsed_s": round(sum(c.elapsed_s for c in self.calls), 1),
        }


def message_chars(messages: list[dict]) -> int:
    return sum(len(m.get("content") or "") for m in messages)


def fit(messages: list[dict], *, budget: int = REQUEST_BUDGET, priorities: list[int] | None = None,
        log=print) -> list[dict]:
    """把消息压进预算——**就地降级，不抛异常**。

    priorities: 与 messages 等长的整数列表，数越小越先被截（默认从后往前截）。
    为什么不用异常兜底：旧管线的 learn 是"超预算就抛"，而那一行外面没有 try/except，
    结果是**整轮 run 死在第一次调用**（0 分），而不是"这一次生成质量差一点"。
    """
    out = [dict(m) for m in messages]
    sizes = [len(m.get("content") or "") for m in out]
    total = sum(sizes)
    if total <= budget:
        return out
    order = list(range(len(out))) if priorities is None else [
        i for _, i in sorted((p, i) for i, p in enumerate(priorities))]
    for idx in order:
        if total <= budget:
            break
        content = out[idx].get("content") or ""
        need = total - budget
        keep = max(400, len(content) - need - 200)
        if keep >= len(content):
            continue
        out[idx]["content"] = content[:keep] + ("\n\n…（已按预算截断；按上面这些实现即可，"
                                               "不要猜被截掉的内容）")
        total = total - len(content) + len(out[idx]["content"])
        log(f"    ⚠️ [fit] 第 {idx + 1} 条消息 {len(content)} → {len(out[idx]['content'])} 字符"
            f"（预算 {budget}，当前 {total}）")
    return out


# 视觉通路的三态开关：
#   None  = 还没试过（**默认状态**：第一张配到图的页面会试一次）
#   True  = 试过且成功 → 后面继续用
#   False = 试过且失败 → 全局熔断，绝不再发图
# 为什么不用"必须给 VISUAL_MODEL 才开"：实测平台那个模型**本身就能吃图**，
# 要求环境变量等于白白丢掉这条通路；而"先试一次+熔断"的最坏代价只是**一次调用**。
# 🔴 **视觉要限量**（2026-09-28 平台实测）：带图调用的墙钟是纯文本的 **3 倍以上**
#    （实测某题单文件 10:28 → 10:35，7 分钟；不带图时约 2 分钟）。
#    官方 github 题有 50 个文件 → 不限量会把整轮拖到**数小时**，而平台那次已经因为时长失败过一次。
#    所以：只给**前 N 个**配到图的文件带图（默认 8），其余走纯文本 ——
#    既吃到"参考截图"的收益，又不让墙钟失控。可用 G2_VISION_MAX 调整。
VISION_MAX = int((os.environ.get("G2_VISION_MAX") or "8").strip() or 8)
VISION_USED = 0
VISION_OK: bool | None = None
VISION_DISABLED = False   # 兼容旧的判断（等价于 VISION_OK is False）


def chat(cfg: LLMConfig, messages: list[dict], *, stage: str, node_id: str = "",
         temperature: float = 0.2, max_tokens: int | None = None, ledger: Ledger | None = None,
         attempts: int = 3, log=print) -> str:
    """发一次 chat/completions。**超预算直接抛**（那是代码 bug，不是运行期意外）。"""
    import requests

    cfg.require_key()
    size = message_chars(messages)
    if size > REQUEST_BUDGET:
        raise LLMError(f"请求 {size} 字符 > 预算 {REQUEST_BUDGET}（stage={stage}）"
                       "—— 拼请求的一侧必须先调 fit()")
    url = f"{cfg.base_url}/chat/completions"
    payload: dict = {"model": cfg.model, "messages": messages, "temperature": temperature,
                     "max_tokens": max_tokens or MAX_OUTPUT_TOKENS}
    if REASONING_EFFORT:
        payload["reasoning_effort"] = REASONING_EFFORT
    last = ""
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        try:
            resp = requests.post(url, json=payload, timeout=cfg.timeout_s,
                                 headers={"Authorization": f"Bearer {cfg.api_key}",
                                          "Content-Type": "application/json"})
            dt = time.time() - t0
            if resp.status_code >= 400:
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if ledger:
                    ledger.add(CallRecord(stage=stage, node_id=node_id, model=cfg.model,
                                          endpoint=url, input_chars=size, elapsed_s=dt,
                                          attempt=attempt, ok=False, error=last))
                # ⚠️ 4xx 里有一类是**可重试**的：网关自己的代理错误（实测多次）。
                # 实测原文：`HTTP 400 {"error":{"code":"proxy_error","message":"... read: connection reset by peer"}}`
                # —— 那是上游连接被重置，不是我们的请求有问题；当成致命错会让整轮 run 白跑。
                retryable_4xx = any(k in last.lower() for k in (
                    "proxy_error", "connection reset", "remote end closed", "timeout", "upstream"))
                if 400 <= resp.status_code < 500 and resp.status_code != 429 and not retryable_4xx:
                    raise LLMError(last)
                time.sleep(min(2 ** attempt, 8))
                continue
            data = resp.json()
            usage = data.get("usage") or {}
            choice = (data.get("choices") or [{}])[0] or {}
            text = (choice.get("message") or {}).get("content") or ""
            # 🔴 记下**截断信号**：`finish_reason == "length"` 表示输出被 token 上限截断。
            #    实测后果极隐蔽：文件写到一半停住 → 模板字符串的反引号只剩一个 →
            #    前端构建失败，而"看起来"只是某个页面写得不好。调用方据此**重问一次精简版**。
            chat.last_finish_reason = str(choice.get("finish_reason") or "")
            details = usage.get("completion_tokens_details") or {}
            if ledger:
                ledger.add(CallRecord(
                    stage=stage, node_id=node_id, model=cfg.model, endpoint=url, input_chars=size,
                    input_tokens=usage.get("prompt_tokens") or 0,
                    output_tokens=usage.get("completion_tokens") or 0,
                    reasoning_tokens=details.get("reasoning_tokens") or 0,
                    total_tokens=usage.get("total_tokens") or 0,
                    elapsed_s=dt, attempt=attempt, ok=True))
            if not text.strip():
                last = "返回内容为空"
                log(f"    ⚠️ [llm] {stage} 第 {attempt} 次返回为空（reasoning 可能吃光了输出）")
                continue
            return text
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 连接层
            dt = time.time() - t0
            last = f"{type(exc).__name__}: {exc}"
            if ledger:
                ledger.add(CallRecord(stage=stage, node_id=node_id, model=cfg.model, endpoint=url,
                                      input_chars=size, elapsed_s=dt, attempt=attempt,
                                      ok=False, error=last))
            log(f"    ⚠️ [llm] {stage} 第 {attempt} 次失败：{last[:120]}")
            time.sleep(min(2 ** attempt, 8))
    raise LLMError(f"{stage} 连续 {attempts} 次失败：{last[:200]}")