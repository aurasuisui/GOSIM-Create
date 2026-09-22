#!/usr/bin/env python3
"""从 bench.sh 产出的原始日志里抽取 RunRecord。

存在理由：docs/04 的 RunRecord 有约 50 个字段，手写一定退化。
先做"从日志能拿到的那部分"，字段先少后多。

用法：
    python eval/extract_run.py runs/<log> --app 12306 --arm baseline
        [--port 3301] [--round 1] [--model X] [--visual-model Y]
        [--change "一句话、可证伪"] [--json-out runs/<record>.json]

字段定义见 docs/04-测量协议.md §5。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ---------- 日志解析 ----------

# "  131 passed (3.6m)"  /  "  4 failed"
RE_SUMMARY = re.compile(r"^\s*(\d+)\s+(passed|failed|skipped|flaky|did not run)\b", re.M)
# "    [chromium] › arc-bench\webapp\12306\tests\REQ-5.3.1.spec.ts:7:5 › REQ-5.3.1: 标题"
RE_FAILED = re.compile(
    r"^\s*\[([\w-]+)\]\s*›\s*(.+?\.spec\.[jt]s):(\d+):(\d+)\s*›\s*(.+?)\s*$", re.M
)
RE_ERROR = re.compile(r"^\s*(?:Error|expect\S*|Test timeout of \d+ms exceeded)[:.].*$", re.M)
RE_REQ_IN_SPEC = re.compile(r"(REQ-[\d.]+)")
# Playwright 重试时的输出标记（"retry #1"、"(retry #2)" 等）
RE_RETRY = re.compile(r"retry\s*#?\s*\d+", re.I)


def classify(errors: list[str]) -> str:
    """把错误文本归到 docs/04 §5 的 failure_class 枚举。"""
    blob = " ".join(errors).lower()
    if not blob:
        return "unknown"
    if "econnrefused" in blob or "err_connection" in blob or "net::err" in blob:
        return "app_not_running"
    if "timeout" in blob and ("exceeded" in blob or "waiting for" in blob):
        return "timeout"
    if "strict mode violation" in blob or "resolved to" in blob and "elements" in blob:
        return "a11y_name_mismatch"
    if "500" in blob or "502" in blob or "503" in blob:
        return "backend_5xx"
    if "tohavetext" in blob or "tohavevalue" in blob or "expected string" in blob:
        return "assertion_text_mismatch"
    if "tobevisible" in blob or "tobechecked" in blob or "tohavetext" in blob:
        return "timeout"          # 断言超时是最常见的形态
    if "not found" in blob or "404" in blob:
        return "route_missing"
    return "unknown"


def parse_log(text: str) -> dict:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "flaky": 0, "did not run": 0}
    for n, kind in RE_SUMMARY.findall(text):
        counts[kind] = int(n)

    errors = [e.strip() for e in RE_ERROR.findall(text)]

    per_test = []
    for _browser, spec_path, line, _col, title in RE_FAILED.findall(text):
        normalized = spec_path.replace("\\", "/")
        spec_file = normalized.split("/")[-1]
        req = RE_REQ_IN_SPEC.search(spec_file) or RE_REQ_IN_SPEC.search(title)
        per_test.append({
            "test_id": f"{spec_file}:{line}",
            "req_id": req.group(1) if req else None,
            "spec_file": spec_file,
            "title": title.strip(),
            "ok": False,                        # 这里只列失败的
            "failure_class": classify(errors),
        })

    total = counts["passed"] + counts["failed"] + counts["flaky"]
    return {
        "tests_total": total or None,
        "tests_passed": counts["passed"],
        "tests_failed": counts["failed"],
        "tests_skipped": counts["skipped"],
        "tests_flaky": counts["flaky"],
        "pass_rate": round(counts["passed"] / total, 4) if total else None,
        "per_test_failed": per_test,
        "failure_class_counts": _count_by(per_test, "failure_class"),
        "retries_observed": len(RE_RETRY.findall(text)),
        "has_summary": bool(total),
    }


def _count_by(items: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for it in items:
        v = it.get(key) or "unknown"
        out[v] = out.get(v, 0) + 1
    return out


# ---------- 环境指纹 ----------

def git_info(path: Path) -> dict:
    def run(*args):
        try:
            r = subprocess.run(["git", "-C", str(path), *args],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None
    commit = run("rev-parse", "HEAD")
    if commit is None:
        return {"git_commit": None, "git_dirty": None, "note": "不是 git 仓库"}
    return {
        "git_commit": commit,
        "git_dirty": bool(run("status", "--porcelain")),
        "git_branch": run("rev-parse", "--abbrev-ref", "HEAD"),
    }


def sha256_of(path: Path) -> str | None:
    if not path.is_file():
        return None
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- 主流程 ----------

def main() -> int:
    ap = argparse.ArgumentParser(description="从 bench 日志抽取 RunRecord")
    ap.add_argument("logfile")
    ap.add_argument("--app", required=True)
    ap.add_argument("--arm", default="manual")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--port", type=int, default=3301)
    ap.add_argument("--model", default=None)
    ap.add_argument("--visual-model", default=None)
    ap.add_argument("--change", default=None, help="一句话、可证伪的改动描述")
    ap.add_argument("--load-snapshot", default=None,
                    help="本轮开始前采的环境快照（status 结论行 / runner 数 / 端口）")
    ap.add_argument("--ci", default=None,
                    help="CI 环境变量的值——它决定 playwright 的 retries（CI 有值 → retries=1）")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    logpath = Path(args.logfile)
    if not logpath.is_absolute():
        logpath = ROOT / logpath
    if not logpath.is_file():
        print(f"找不到日志：{logpath}", file=sys.stderr)
        return 1

    text = logpath.read_text(encoding="utf-8", errors="replace")
    parsed = parse_log(text)

    bench_dir = ROOT / "repos" / "arc-bench"
    req_yaml = bench_dir / "arc-bench" / "webapp" / args.app / "requirements" / "requirements.yaml"

    record = {
        "run_id": logpath.stem,
        "arm_id": args.arm,
        "round": args.round,
        "change_description": args.change,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),

        "app": args.app,
        "port": args.port,
        # 测试是日期相关的，这个字段必须记下来（否则两轮读数不可比）。
        # 原来写死 `None` + 注释"由调用方填写"，而**没有任何调用方填**——
        # r3a-1 的记录里就是 None，而同一轮的脚本日志明明打了 ARC_TEST_DATE=2026-09-20。
        # score_app.sh 已经 export 了它 → 直接从环境读，别再指望别人手填。
        "arc_test_date": os.environ.get("ARC_TEST_DATE") or None,
        "model": args.model,
        "visual_model": args.visual_model,

        # 测量条件（第六轮审核要求：R5 与 R1–R4 原本无法从记录里区分）
        # playwright.config.ts: `retries: process.env.CI ? 1 : 0`
        # 所以 CI 有值 → 重试 1 次；无 CI → 0 次。口径不同就不能直接比。
        "ci_env": args.ci,
        "retries_effective": 1 if (args.ci and args.ci.strip().lower() not in ("0", "false", "")) else 0,
        "load_snapshot": args.load_snapshot,

        "pipeline": git_info(ROOT),
        "bench_repo": git_info(bench_dir),
        "requirements_sha256": sha256_of(req_yaml),

        "log_path": str(logpath.relative_to(ROOT)).replace("\\", "/"),
        **parsed,
    }

    if not parsed["has_summary"]:
        record["WARNING"] = "日志里没有测试汇总——这一轮可能没跑完"

    out = Path(args.json_out) if args.json_out else logpath.with_suffix(".json")
    if not out.is_absolute():
        out = ROOT / out
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    # 摘要打到 stdout，方便 bench.sh 里一眼看见
    rate = parsed["pass_rate"]
    print(f"  tests      : {parsed['tests_passed']} passed / {parsed['tests_failed']} failed"
          f" (total {parsed['tests_total']})")
    print(f"  pass_rate  : {rate if rate is not None else '—'}")
    print(f"  失败分类   : {parsed['failure_class_counts'] or '—'}")
    print(f"  RunRecord  → {out.relative_to(ROOT)}")
    if "WARNING" in record:
        print(f"  ⚠️  {record['WARNING']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
