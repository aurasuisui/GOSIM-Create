"""轮询平台 submit 的 run 状态，直到 settle（用于拿真实平台分数）。

用法：python g2/tools/watch_runs.py <submission_id> [轮数]
"""
from __future__ import annotations

import json
import sys
import time

import requests

EMAIL = "2436448088@qq.com"
PASSWORD = "xbao2436"
BASE = "https://arc-bench.com"


def main(argv: list[str]) -> int:
    sub = argv[1] if len(argv) > 1 else "87bd1f7e0aef"
    rounds = int(argv[2]) if len(argv) > 2 else 180
    s = requests.Session()
    r = s.post(BASE + "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    print("login:", r.status_code, flush=True)
    for i in range(rounds):
        try:
            runs = s.get(BASE + "/api/runs", timeout=30).json()
        except Exception as exc:  # noqa: BLE001
            print("poll error:", type(exc).__name__, flush=True)
            time.sleep(60)
            continue
        mine = [x for x in runs if x.get("submission_id") == sub]
        busy = [x for x in mine if x.get("status") == "RUNNING"]
        line = "  ".join(f"{x.get('requirement_id')}={x.get('status')}"
                          f"(passed={x.get('passed_count')}/fail={x.get('failed_count')}"
                          f",score={x.get('score')},rate={x.get('test_pass_rate')},"
                          f"feat={x.get('feature_implementation_rate')})" for x in mine)
        print("[" + time.strftime("%H:%M:%S") + "] " + line, flush=True)
        if not busy:
            print("ALL SETTLED", flush=True)
            break
        time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))