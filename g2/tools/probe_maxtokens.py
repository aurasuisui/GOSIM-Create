import os, sys, requests
sys.path.insert(0, "g2")
from app.gen.llm import LLMConfig
cfg = LLMConfig.from_env()
for mt in (16000, 32000):
    payload = {"model": cfg.model, "max_tokens": mt,
               "messages": [{"role": "user", "content": "Reply with exactly: OK"}]}
    try:
        r = requests.post(cfg.base_url.rstrip("/") + "/chat/completions",
                          headers={"Authorization": "Bearer " + cfg.api_key}, json=payload, timeout=90)
        ok = r.status_code == 200
        print(mt, "->", r.status_code, (r.text or "")[:110].replace(chr(10), " ") if not ok else "accepted")
    except Exception as e:
        print(mt, "-> EXC", type(e).__name__)