"""本地判分：起应用 → 跑官方测试 → 落一份机器可读结果。

用法：python g2/tools/score.py <app> <app_dir> [--label L] [--port P] [--no-install] [--no-build]

跑测试的方式沿用基准仓库自己的 runner：
    cd repos/arc-bench && npm run test -- --app <app> --target-url http://127.0.0.1:<port>
（它是只读参考仓库里现成的一套接线，改它没有意义；我们只读它的输出。）

纪律（沿用且仍然有效的两条）：
  ① 每轮先删数据库文件 —— sqlite 状态跨轮累积，实测能让同一份产物从 0 失败变成 15 失败；
  ② 固定 ARC_TEST_DATE —— 测试是日期相关的。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tasks import get  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "repos/arc-bench"
NPM = "npm.cmd" if os.name == "nt" else "npm"


def log(msg: str) -> None:
    print(f"[score] {msg}", flush=True)


def wait_port(port: int, timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def probe(url: str, timeout: float = 5) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}"


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, text=True)
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()


RESULT_RE = re.compile(r"^\s*(?:✓|✘|×|✔|✗|\u2713|\u2717|\u00d7)?\s*(\d+)?\s*\[?([a-zA-Z0-9_.\-]+)\]?\s*(.*)$")


def parse_playwright(text: str) -> dict:
    """从 list reporter 输出里取逐条结果与汇总行。

    两种行都要认（实测都出现过）：
      `  ✓  1 [bookstack] › REQ-1.1.spec.ts:7:5 › REQ-1.1: Open Homepage (1.2s)`
      `  1 passed (12.3s)` / `  32 failed` / `  3 flaky`
    """
    passed = re.findall(r"^\s*(?:\u2713|✓|✔)\s*(.*?)\s*(?:\(([\d.]+)m?s\))?\s*$", text, re.M)
    failed = re.findall(r"^\s*(?:\u2717|✘|×|✗)\s*(.*?)\s*(?:\(([\d.]+)m?s\))?\s*$", text, re.M)
    summary = {}
    for key in ("passed", "failed", "flaky", "skipped", "did not run", "interrupted"):
        m = re.search(rf"^\s*(\d+)\s+{key}\b", text, re.M)
        if m:
            summary[key.replace(" ", "_")] = int(m.group(1))
    return {
        "passed_count": summary.get("passed", len(passed)),
        "failed_count": summary.get("failed", len(failed)),
        "flaky_count": summary.get("flaky", 0),
        "skipped_count": summary.get("skipped", 0),
        "passed_titles": [p[0].strip() for p in passed][:200],
        "failed_titles": [f[0].strip() for f in failed][:200],
        "summary": summary,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("app")
    ap.add_argument("app_dir")
    ap.add_argument("--label", default="manual")
    ap.add_argument("--port", type=int, default=3301)
    ap.add_argument("--no-install", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--timeout-s", type=int, default=3600)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv[1:])

    task = get(args.app)
    app_dir = Path(args.app_dir).resolve()
    if not (app_dir / "frontend").is_dir() or not (app_dir / "backend").is_dir():
        log(f"❌ 不是应用目录（缺 frontend/ 或 backend/）：{app_dir}")
        return 2
    if not (BENCH / "package.json").is_file():
        log(f"❌ 基准仓库不在：{BENCH}")
        return 2

    os.environ.setdefault("ARC_TEST_DATE", "2026-09-20")
    t0 = time.time()
    result: dict = {
        "app": args.app, "label": args.label, "app_dir": str(app_dir),
        "arc_test_date": os.environ["ARC_TEST_DATE"], "port": args.port,
        "official_tests": task.tests,
    }

    removed = []
    for p in (app_dir / "backend").glob("*.db*"):
        try:
            p.unlink(); removed.append(p.name)
        except OSError:
            pass
    result["db_removed"] = removed
    log(f"清库：{removed or '（无旧库）'}")

    # npm 11 对**用户级 .npmrc** 里的 `allow-scripts=...` 会在 project-scoped install 上直接报错
    # （`EALLOWSCRIPTS: --allow-scripts is not allowed in project-scoped installs`）。
    # 那是这台机器上 dsh 自己的配置，与我们的产物无关 → 用**独立 userconfig** 隔离掉，
    # 而不是去改用户的全局配置。
    iso_npmrc = Path(tempfile.gettempdir()) / "g2-npmrc-empty"
    iso_npmrc.write_text("", encoding="utf-8")
    # 关键的一条：把 allow-scripts **显式置空**（env 覆盖优先于任何 npmrc）。
    # 实测：只隔离 userconfig 不够——`npm config list -l` 里 `allow-scripts` 仍显示 user 来源。
    npm_env = {**os.environ, "NPM_CONFIG_USERCONFIG": str(iso_npmrc),
               "npm_config_userconfig": str(iso_npmrc),
               "npm_config_allow_scripts": "", "NPM_CONFIG_ALLOW_SCRIPTS": ""}

    def npm(cwd: Path, argv_: list[str], timeout: int = 1200):
        proc = subprocess.run([NPM, *argv_], cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, shell=False, env=npm_env)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    if not args.no_install:
        for sub in ("backend", "frontend"):
            log(f"装依赖：{sub}")
            rc, out = npm(app_dir / sub, ["install", "--no-audit", "--no-fund"])
            if rc != 0:
                log(f"❌ {sub} 装依赖失败（尾部 25 行）:\n" + "\n".join(out.splitlines()[-25:]))
                result.update({"stage": f"install_{sub}", "ok": False, "install_tail": out[-3000:]})
                _write(args, result); return 1

    if not args.no_build:
        log("构建前端")
        rc, out = npm(app_dir / "frontend", ["run", "build"])
        result["build_rc"] = rc
        result["build_tail"] = "\n".join(out.splitlines()[-15:])
        if rc != 0:
            log("❌ 前端构建失败:\n" + result["build_tail"])
            result.update({"stage": "build", "ok": False})
            _write(args, result); return 1

    log(f"起后端（PORT={args.port}）")
    env = dict(os.environ); env["PORT"] = str(args.port)
    # 后端日志必须落盘：实测后端会在跑测试中途退出（24/34 条变成 ERR_CONNECTION_REFUSED），
    # 而之前输出被丢进 PIPE 没人读 → 事后完全看不到崩因。
    backend_log = ROOT / "g2/runs" / f"{args.app}-{args.label}.backend.log"
    backend_log.parent.mkdir(parents=True, exist_ok=True)
    backend_fh = backend_log.open("w", encoding="utf-8", errors="replace")
    backend = subprocess.Popen([NPM, "start"], cwd=str(app_dir / "backend"), env=env,
                               stdout=backend_fh, stderr=subprocess.STDOUT, text=True)
    try:
        if not wait_port(args.port, 120):
            log("❌ 后端 120 秒内没起来")
            result.update({"stage": "backend", "ok": False})
            _write(args, result); return 1
        result["smoke"] = {"root": probe(f"http://127.0.0.1:{args.port}/")}
        log(f"  冒烟 / → {result['smoke']['root']}")

        log(f"跑官方测试：{args.app}（{task.tests} 条）")
        cmd = [NPM, "run", "test", "--", "--app", args.app,
               "--target-url", f"http://127.0.0.1:{args.port}"]
        result["tests_cmd"] = " ".join(cmd)
        proc = subprocess.run(cmd, cwd=str(BENCH), capture_output=True, text=True,
                              timeout=args.timeout_s, shell=False)
        raw = (proc.stdout or "") + (proc.stderr or "")
        log_file = ROOT / "g2/runs" / f"{args.app}-{args.label}.playwright.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(raw, encoding="utf-8", errors="replace")
        result["tests_rc"] = proc.returncode
        result["playwright_log"] = str(log_file)
        result["playwright"] = parse_playwright(raw)
        result["ok"] = True; result["stage"] = "tests"
        pw = result["playwright"]
        log(f"  通过 {pw['passed_count']} / 失败 {pw['failed_count']} / 跳过 {pw['skipped_count']}"
            f"  （rc={proc.returncode}）")
    finally:
        kill_tree(backend)
        try:
            backend_fh.close()
        except Exception:  # noqa: BLE001
            pass
        result["backend_log"] = str(backend_log)

    result["elapsed_s"] = round(time.time() - t0, 1)
    _write(args, result)
    return 0


def _write(args, result: dict) -> None:
    out = Path(args.out) if args.out else (ROOT / "g2/runs" / f"{args.app}-{args.label}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"结果 → {out}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))