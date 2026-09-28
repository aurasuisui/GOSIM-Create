"""冒烟闸门：起应用 → 跑 smoke.js（10 秒）→ 给出可读结论。

用法：python g2/tools/smoke.py <app_dir> [--port P] [--label L]
退出码：0 = 冒烟过；1 = 冒烟发现问题（**此时不要花 16 分钟去跑判分**）。

为什么把它排在判分之前：判分一轮 16 分钟且几乎全在等超时，
而本轮实测的致命问题（首页没有 Login 入口）**10 秒就能测出来**。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NPM = "npm.cmd" if os.name == "nt" else "npm"


def wait_port(port: int, timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app_dir")
    ap.add_argument("--port", type=int, default=3321)
    ap.add_argument("--label", default="smoke")
    ap.add_argument("--packs-dir", default="g2/packs")
    ap.add_argument("--app", default="bookstack")
    args = ap.parse_args(argv[1:])

    app_dir = Path(args.app_dir).resolve()
    arc = app_dir / ".arc"
    if not (arc / "nav_targets.json").is_file():
        print("❌ 没有 .arc/nav_targets.json —— 先跑 compile/plan（它是判据的契约来源）")
        return 1
    tests_dir = Path(args.packs_dir) / args.app / "tests"
    sys.path.insert(0, str(ROOT / "g2"))
    from app.gen.seed import load_fixtures  # noqa: E402
    fx = load_fixtures(tests_dir)
    fx_path = arc / "smoke_fixtures.json"
    fx_path.write_text(json.dumps(fx, ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ, PORT=str(args.port))
    proc = subprocess.Popen([NPM, "start"], cwd=str(app_dir / "backend"), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_port(args.port, 90):
            print("❌ 后端 90 秒内没起来 —— 先看 backend 日志（这类问题判分也会全挂）")
            return 1
        cmd = ["node", str(ROOT / "g2" / "tools" / "smoke.js"), f"http://127.0.0.1:{args.port}/",
               str(fx_path), str(arc / "nav_targets.json"), str(arc / "assert_texts.json")]
        # playwright 没装在 g2 下：用基准仓库那份已装的（`npm install --no-save @playwright/test`）。
        # 用 NODE_PATH 而不是复制依赖：`repos/` 是只读参考，不该被改。
        smoke_env = dict(os.environ)
        smoke_env["NODE_PATH"] = str(ROOT / "repos" / "arc-bench" / "node_modules")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=smoke_env,
                             cwd=str(ROOT / "repos" / "arc-bench"))
        print(res.stdout[-4000:] or res.stderr[-2000:])
        out = arc / f"smoke-{args.label}.json"
        out.write_text(res.stdout, encoding="utf-8")
        print(f"结论 → {out}  (rc={res.returncode})")
        return 0 if res.returncode == 0 else 1
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))