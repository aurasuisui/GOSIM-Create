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
# **通过的是哪几条**：list 报告里每条用例一行（`ok` / `x`）——
# 为什么必须落盘：两轮都是 1/32，但通过的不是同一条（`REQ-2.1` vs `REQ-2.7.3`）
# ——**通过数会掩盖"分数搬家"**；子集之外的偶然通过也不是能力证据。
# ⚠️ `[浏览器]` 那一段是**可选**的：同一个 playwright 有两种 list 形态
#   · keep（ARC webapp）：`ok 1 [chromium] › arc-bench\webapp\keep\tests\REQ-1.1.spec.ts:7:5 › …`
#   · quickstart（Lab04）：`ok 1 REQ-1-user-registration.spec.ts:48:5 › …`（**没有**浏览器标记）
# 少了这个 `?`，quickstart 那 8 份日志一条逐条结果都抽不到（而汇总行有数）——
# 又一次"判据没报错 ≠ 判据跑过了"。
RE_TEST_LINE = re.compile(
    r"^\s*(ok|x|✓|✘)\s+\d+\s+(?:\[([\w-]+)\]\s*›\s*)?(.+?\.spec\.[jt]s):(\d+):(\d+)\s*›\s*(.+?)\s*$", re.M)
# "    [chromium] › arc-bench\webapp\12306\tests\REQ-5.3.1.spec.ts:7:5 › REQ-5.3.1: 标题"
RE_FAILED = re.compile(
    r"^\s*\[([\w-]+)\]\s*›\s*(.+?\.spec\.[jt]s):(\d+):(\d+)\s*›\s*(.+?)\s*$", re.M
)
RE_ERROR = re.compile(r"^\s*(?:Error|expect\S*|Test timeout of \d+ms exceeded)[:.].*$", re.M)
# **日志自报的独立总数**（Playwright 的 `Running N tests using M workers`）——
# 它不经过我们自己的汇总行解析，所以能当对照量（守恒自检的"另一端"）。
RE_RUNNING = re.compile(r"Running (\d+) tests")
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

    # 日志自报总数（取**最后一次**：汇总行也是最后一次生效，两者配成同一段输出）
    running = [int(n) for n in RE_RUNNING.findall(text)]
    log_total_running = running[-1] if running else None

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
    per_test_passed = [
        {"test_id": f"{sp.replace(chr(92), '/').split('/')[-1]}:{ln}", "title": title.strip()}
        for mark, _b, sp, ln, _c, title in RE_TEST_LINE.findall(text) if mark.lower() == "ok"
    ]
    out = {
        "tests_total": total or None,
        "tests_passed": counts["passed"],
        "tests_failed": counts["failed"],
        "tests_skipped": counts["skipped"],
        "tests_flaky": counts["flaky"],
        "pass_rate": round(counts["passed"] / total, 4) if total else None,
        # 列表格式的日志里，失败行带 `x` 前缀 → RE_FAILED（开头就是 `[browser]`）匹配不到，
        # 于是 `per_test_failed` 一直是空的（而 `tests_failed` 有数）。这里用同一个来源补齐，
        # 保证"通过集合 / 失败集合"对称、口径一致。
        "per_test_failed": per_test or [
            {"test_id": f"{sp.replace(chr(92), '/').split('/')[-1]}:{ln}",
             "title": title.strip(), "failure_class": "unknown"}
            for mark, _b, sp, ln, _c, title in RE_TEST_LINE.findall(text) if mark.lower() == "x"
        ],
        # 通过集合（同一口径的对称项）——**读数必须连它一起记**
        "per_test_passed": per_test_passed,
        # 日志自报的独立总数（对照量）：抽取器算出来的 `tests_total` 要能被它解释
        "log_total_running": log_total_running,
        "failure_class_counts": _count_by(per_test, "failure_class"),
        "retries_observed": len(RE_RETRY.findall(text)),
        "has_summary": bool(total),
    }

    # === 守恒自检（2026-09-22 改；**这一版才可证伪**）===
    # 判据：**日志自报的 `Running N tests` 必须能被我们的计数解释**——
    # 要么等于 `passed+failed+flaky`（Playwright 的 total 不含 skipped），
    # 要么等于再算上 skipped 的口径。两个都不等 → 抽取器算错了。
    #
    # 为什么改：第一版判据写的是 `passed+failed+skipped+flaky == passed+failed+flaky`，
    # 即 `counted - total ≡ skipped` ——**恒真式**（skipped=0 时永远成立）。
    # 实测它的两头都错：把 `31 failed` 改成 `3 failed`（模拟"汇总行读错"）它**一声不响**；
    # 而一条合法的 `3 skipped` 会让它喊"抽取器坏了"（误报）。**方向恰好是反的。**
    # 现在拿日志里的独立总数当对照 → 两类都能判对。
    counted = (counts["passed"] + counts["failed"] + counts["skipped"] + counts["flaky"])
    if log_total_running is not None and log_total_running not in (total, counted):
        print(f"  ⚠️  守恒自检不通过：日志自报 `Running {log_total_running} tests`，"
              f"而我们数出来 passed+failed+flaky = {total}"
              f"（含 skipped 的口径 = {counted}）——两个口径都解释不了它，"
              "别拿这份记录下结论", file=sys.stderr)
    elif log_total_running is None and total:
        print("  ⚠️  守恒自检：日志里**没有** `Running N tests`（独立总数缺失）→"
              "本次只核到「逐条 vs 汇总」这一层，拿它跟别的轮次比时要留意", file=sys.stderr)
    if counts["skipped"]:
        # 这**不是**错误，是分母口径：记录里的 tests_total 不含 skipped。
        print(f"  提示：本轮有 {counts['skipped']} 条 skipped——Playwright 的 total 不含 skipped，"
              f"所以 tests_total={total}、日志自报 Running={log_total_running}", file=sys.stderr)
    if total and per_test_passed == [] and per_test == []:
        print("  ⚠️  守恒自检：有汇总行却**一条逐条结果都抽不到**"
              "（解析器与日志格式对不上）", file=sys.stderr)
    return out


def _count_by(items: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for it in items:
        v = it.get(key) or "unknown"
        out[v] = out.get(v, 0) + 1
    return out


# ---------- 环境指纹 ----------

def subject_of(commit: str | None, repo: Path) -> str | None:
    """按 commit 反查 subject（出生处没记 subject 的老产物走这条）。

    ⚠️ **只对仍然可达的 commit 有效**：历史重排/阶段合并之后，旧 hash 查不到 → 返回 None。
    这正是"要连 subject 一起记"的原因（第三十轮审核 §三 A）——但**查不到就写 None，别编**。
    """
    if not commit:
        return None
    try:
        r = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s", commit],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


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
        "git_subject": run("log", "-1", "--format=%s"),   # 跨重排活下来的那半（见 provenance.py）
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


def read_provenance(app_dir) -> dict | None:
    """读**产物出生处**的指纹（`<app>/.arc/provenance.json`，由 `pipeline/provenance.py` 写）。

    为什么要优先读它（第二十三轮审核 §三 的根因）：`git_info()` 原来是在**写记录时**现采的，
    而记录通常在判分跑完之后（约一小时）才落盘 → 它 attest 的是"那一刻的工作区"，
    不是"产出这份产物时代码长什么样"。实测过的自相矛盾：产物干净、记录却 `git_dirty=true`。
    """
    if not app_dir:
        return None
    p = Path(app_dir) / ".arc" / "provenance.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _pipeline_block(prov: dict | None, root: Path) -> dict:
    """记录里的 `pipeline` 块：产物出生处的指纹优先，否则退回现采。

    读不到时**不静默**：`provenance_source` 会写成 `record-time`，
    并且这里把"这个值 attest 的是写记录那一刻"显式留在同一个块里（`attests` 字段）。
    """
    if prov:
        return {
            "git_commit": prov.get("git_commit"),
            "git_dirty": prov.get("git_dirty"),
            # ⚠️ **必须带上**（2026-09-23 补）：它是"仓库脏"与"**产出产物的那份代码**脏"的区分字段，
            # 而**记录**才是筛轮次的人要看的地方。实测漏过一次：两份记录的 `pipeline` 块里没有它，
            # 而它只在产物的 `.arc/provenance.json` 里（于是"要判它就得去翻产物"）。
            "pipeline_dirty": prov.get("pipeline_dirty"),
            "git_branch": prov.get("git_branch"),
            # subject 跨"历史重排/阶段合并"活下来（hash 不会）——见 provenance.py 的说明
            "git_subject": prov.get("git_subject") or subject_of(prov.get("git_commit"), root),
            "attests": "product-birth",              # 这个值说的是"产出它的代码"
            "captured_at_product": prov.get("written_at"),
            "app_hint": prov.get("app_hint"),
            "req_ids": prov.get("req_ids"),
            "generate_mode": prov.get("generate_mode"),
            "stage_seconds": prov.get("stage_seconds"),
            # 成本侧两个数**分开**（第二十四轮审核 §三 A）：闭环占全程 51–58%，
            # 只有 `tokens_total` 才是 pass/CNY 的分母。
            "tokens_total": prov.get("tokens_total"),
            "tokens_generation_only": prov.get("tokens_generation_only"),
            "verify_ran": prov.get("verify_ran"),
        }
    got = git_info(root)
    got["attests"] = "record-time"                   # ⚠️ 不是产物出生时的工作区
    got["pipeline_dirty"] = None                     # 现采时没有"只算 pipeline/"的口径 → 显式置空，别让读者以为是 False
    return got


def _artifact_fingerprint(prov: dict | None, block: dict, root: Path) -> dict:
    """**机器可读**的「产出这份产物的代码干不干净」。

    为什么单列一个字段：`pipeline.git_dirty` 的语义随来源变化（产物出生处 vs 写记录时），
    而筛轮次的人/脚本需要一句能直接判的话。两者都写清：

        stamped_at_product = True  → 由产物自带的 `.arc/provenance.json` 给出（首选）
        stamped_at_product = False → 是**写记录那一刻**的现采（attest 不了产物）

    `code_clean` 说的是**产出产物的那份代码**；`basis` 写清它是怎么来的。
    """
    commit = (prov or {}).get("git_commit") or block.get("git_commit")
    if prov:
        # code_clean 问的是「**产出它的那份代码**干不干净」→ 有 pipeline_dirty 就用它
        # （仓库脏可能只是文档在飞：实测过 PLAN.md 在别的会话手里、而 pipeline/ 干净）。
        # 老产物没有这个字段时才退回仓库级 git_dirty。
        _precise = prov.get("pipeline_dirty")
        dirty = _precise if _precise is not None else prov.get("git_dirty")
        return {"git_commit": commit, "code_clean": (not dirty) if dirty is not None else None,
                "basis": "product-birth stamp（.arc/provenance.json）"
                         + ("，按 pipeline/ 判定" if _precise is not None
                            else "，**退回仓库级**（该产物早于 pipeline_dirty 字段）"),
                "stamped_at_product": True}
    dirty = block.get("git_dirty")
    return {"git_commit": commit, "code_clean": (not dirty) if dirty is not None else None,
            "basis": "record-time capture（attests 的是写记录那一刻的工作区，不是产物出生时）",
            "stamped_at_product": False,
            "hint": "要判「产出它的代码干不干净」，用 git log --oneline <commit>..HEAD -- pipeline/ 看有没有改动"}


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
                    help="本轮开始前采的环境快照（**机器可读形式**：status=free runners=0 port=free listeners=0）")
    ap.add_argument("--snapshot-note", default=None,
                    help="快照的补充说明（写进记录本身：它是什么时候、在什么状态下采的）")
    ap.add_argument("--app-dir", default=None,
                    help="被打分应用的目录——用来读**产物出生处**的指纹（.arc/provenance.json）")
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
    prov = read_provenance(args.app_dir)

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
        "snapshot_note": args.snapshot_note,

        # ---- 指纹：**优先产物出生处**，读不到才退回"写记录时现采" ----
        # 记录里必须能看出用的是哪一个（`provenance_source`），否则又变成"记录不能自证"。
        "pipeline": _pipeline_block(prov, ROOT),
        "provenance_source": "stage-artifact" if prov else "record-time",
        "pipeline_at_record_time": git_info(ROOT) if prov else None,
        # 机器可读的"产出它的代码干不干净"（筛轮次的人/脚本读这一句，不必解读上面对比两个来源）
        "artifact_fingerprint": _artifact_fingerprint(prov, _pipeline_block(prov, ROOT), ROOT),
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
    _c = str((record.get("pipeline") or {}).get("git_commit") or "?")
    print(f"  指纹来源   : {record['provenance_source']}"
          f"（commit={_c[:8]}，attests={(record.get('pipeline') or {}).get('attests')}）")
    try:
        shown = out.relative_to(ROOT)
    except ValueError:            # 写到工作区外的临时文件时（自检/对比用）不该崩
        shown = out
    print(f"  RunRecord  → {shown}")
    if "WARNING" in record:
        print(f"  ⚠️  {record['WARNING']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
