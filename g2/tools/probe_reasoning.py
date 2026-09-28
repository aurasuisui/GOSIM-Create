"""标定：同一个写作请求在 reasoning_effort 三档下的耗时与用量（决定全量生成的墙钟）。"""
import json, sys, time, urllib.request
sys.path.insert(0, "g2")
from app.gen.llm import LLMConfig  # noqa: E402

cfg = LLMConfig.from_env()
cfg.require_key()
PROMPT = ("写一个 React 组件文件 frontend/src/pages/ItemsPage.tsx：显示标题 Items、"
          "一个按钮 New Item、一个带 label 的输入框 Name，并在下方列出 items 数组的每一条。")


def call(extra):
    body = {"model": cfg.model, "messages": [{"role": "user", "content": PROMPT}],
            "temperature": 0.2, "max_tokens": 12000}
    if extra:
        body.update(extra)
    req = urllib.request.Request(cfg.base_url + "/chat/completions",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + cfg.api_key)
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read().decode())
    dt = time.time() - t0
    u = data.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    txt = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return {"elapsed_s": round(dt, 1), "in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
            "reasoning": det.get("reasoning_tokens"), "chars": len(txt)}


for label, extra in [("default", None), ("effort=low", {"reasoning_effort": "low"}),
                     ("effort=minimal", {"reasoning_effort": "minimal"})]:
    try:
        print(label, call(extra), flush=True)
    except Exception as exc:
        print(label, "FAILED", type(exc).__name__, str(exc)[:160], flush=True)