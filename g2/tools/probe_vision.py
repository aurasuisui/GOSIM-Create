import base64, json, os, sys
from pathlib import Path
sys.path.insert(0, "g2")
from app.gen.llm import LLMConfig
import requests
cfg = LLMConfig.from_env()
print("model:", cfg.model, "| base:", cfg.base_url)
img = Path(sys.argv[1])
b64 = base64.b64encode(img.read_bytes()).decode()
for model in [os.environ.get("VISUAL_MODEL") or "", cfg.model]:
    if not model:
        continue
    payload = {"model": model, "max_tokens": 40, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What UI is in this screenshot? Answer in 8 words."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]}
    try:
        r = requests.post(cfg.base_url.rstrip("/") + "/chat/completions",
                          headers={"Authorization": "Bearer " + cfg.api_key, "Content-Type": "application/json"},
                          json=payload, timeout=200)
        print(model, "->", r.status_code, (r.text or "")[:180].replace("\n", " "))
    except Exception as e:
        print(model, "-> EXC", type(e).__name__, str(e)[:120])
