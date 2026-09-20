#!/usr/bin/env python3
"""入口 —— 平台的固定调用契约（`docs/02` §一）。

## 契约（不能动）

- 本文件必须在提交 zip 的**第一层**（不是 `src/main.py`）。
- 同层**不能有** `package.json` / `index.js` / `index.ts`——有的话 runner
  会切到 Node 入口路径。
- Python 依赖由平台在跑之前按 `requirements.txt` 装好（那一阶段有网络）；
  **agent 真正跑起来之后没有网络**。

## 参数来源：CLI 与环境变量都收

`arcbench_agent_runtime/context.py:26-55` 读这些环境变量：

    ARCBENCH_OUTPUT_DIR / ARCBENCH_PROJECT_DIR / ARCBENCH_TEMPLATE_DIR
    ARCBENCH_RUNNER_EVENTS_PATH   （默认 .arc/runner-events.jsonl）
    ARCBENCH_TRACEABILITY_DIR     （默认 .arc/traceability）

而 ARC 自己的 `main.py` 用的是 **CLI 参数**（`compile <path> -o DIR`）。
平台到底走哪条路，目前**没有确证**（`docs/02` §一把它列为待验证项）。
所以两边都支持：

    CLI 给了就用 CLI，没给就落到环境变量，都没有才报错。

日志里会把每个值的来源打出来——这样一次真实提交就能同时把
"平台到底传了什么"这个问题回答掉，不必再单独跑探针。

## 本阶段做什么

第 1 阶段 = 需求编译（**纯代码，0 LLM 调用**）：解析需求树、校验、
建场景索引、抽可访问名契约，并把结果写进 traceability。
后面几个阶段（设计 / 生成 / 验证）在此基础上接。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# 把包目录放进 import 路径——平台可能从别处调用本文件
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from arc_runtime import AgentRuntime  # noqa: E402
from reqcompile import (  # noqa: E402
    build_scenario_index,
    extract_accessible_names,
    load_requirement_tree,
)

DEFAULT_WEB_PORT = 3000


# ---------- 参数解析（CLI + 环境变量） ----------

@dataclass
class ResolvedInputs:
    """解析好的输入，附带每个值来自哪里——日志要打出来。"""

    requirement_path: Path
    output_dir: Path
    sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _first_env(*names: str) -> tuple[str, str] | None:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v, n
    return None


def _resolve_requirement(cli_value: str | None, sources: dict[str, str]) -> Path | None:
    if cli_value:
        sources["requirement_path"] = "CLI"
        return Path(cli_value).expanduser().resolve()
    hit = _first_env(
        "ARCBENCH_REQUIREMENTS_PATH", "ARCBENCH_REQUIREMENT_PATH",
        "ARCBENCH_REQUIREMENTS_DIR", "ARCBENCH_REQUIREMENT_DIR",
    )
    if hit:
        sources["requirement_path"] = f"env:{hit[1]}"
        return Path(hit[0]).expanduser().resolve()
    return None


def _resolve_output(cli_value: str | None, sources: dict[str, str]) -> Path:
    if cli_value:
        sources["output_dir"] = "CLI"
        return Path(cli_value).expanduser().resolve()
    hit = _first_env(
        "ARCBENCH_OUTPUT_DIR", "ARCBENCH_PROJECT_DIR", "ARCBENCH_TEMPLATE_DIR",
    )
    if hit:
        sources["output_dir"] = f"env:{hit[1]}"
        return Path(hit[0]).expanduser().resolve()
    sources["output_dir"] = "default:."
    return Path(".").resolve()


def resolve_inputs(args: argparse.Namespace) -> ResolvedInputs:
    sources: dict[str, str] = {}
    notes: list[str] = []

    req = _resolve_requirement(getattr(args, "requirement_path", None), sources)
    if req is None and getattr(args, "requirement_dir", None):
        req = Path(args.requirement_dir).expanduser().resolve()
        sources["requirement_path"] = "CLI(--requirement-dir)"
    if req is None:
        # 最后的兜底：输出目录里如果正好有一份，就用它
        out_probe = _resolve_output(args.output_dir, {})
        for cand in ("requirements.yaml", "requirements.yml"):
            if (out_probe / cand).is_file():
                req = out_probe / cand
                sources["requirement_path"] = "found-in-output-dir"
                notes.append("需求文件是从输出目录里猜出来的，最好显式传 --requirement-path")
                break
    if req is None:
        raise SystemExit(
            "找不到需求文件：CLI 没给，环境变量里也没有 "
            "ARCBENCH_REQUIREMENTS_PATH / _DIR。"
        )

    out = _resolve_output(args.output_dir, sources)
    return ResolvedInputs(requirement_path=req, output_dir=out, sources=sources, notes=notes)


# ---------- 第 1 阶段 ----------

def run_stage1(runtime: AgentRuntime, inputs: ResolvedInputs, work_dir: Path):
    """需求编译。返回 (产物摘要, 需求树, 可访问名索引)——后两者交给生成阶段用。"""
    log = print

    log(f"  需求文件: {inputs.requirement_path}")
    log(f"  输出目录: {inputs.output_dir}")

    tree, report = load_requirement_tree(inputs.requirement_path)
    log(f"  需求树  : {len(tree.nodes)} 节点 / {len(tree.leaves())} 叶子 "
        f"/ sha256={tree.source_sha256[:12]}…")

    # YAML 修过就说出来——我们改了输入，不能悄悄改
    if report.repairs:
        for r in report.repairs:
            for fix in r.repairs:
                log(f"  ⚠️  YAML 缩进修复: 第 {fix.line} 行 {fix.key!r} "
                    f"{fix.old_indent}→{fix.new_indent} 格")

    for issue in report.warnings:
        log(f"  ⚠️  [{issue.code}] {issue.message}")

    if not report.ok:
        for issue in report.errors:
            log(f"  ❌ [{issue.code}] {issue.message}")
        # 有 error 也继续写 traceability：让平台上能看见坏在哪，
        # 比静默退出更有用。但阶段标记为失败。
        for issue in report.errors:
            if issue.node_id:
                runtime.events.mark_design_failed(issue.node_id, issue.message)

    index = build_scenario_index(tree)
    a11y = extract_accessible_names(tree)
    log(f"  场景索引: {len(index.entries)} 条（= 预期外部测试条数）")
    log(f"  可访问名: {len(a11y.entries)} 条 / {len(a11y.unique_names())} 个去重名")

    dupes = index.duplicate_names()
    if dupes:
        log(f"  ⚠️  场景重名 {len(dupes)} 组（跨节点无害，同节点内是真问题）")

    # ---- 写 traceability（必须走 SDK 高阶方法，不得手工拼 payload）----
    runtime.traceability.store_requirement_tree(tree.to_nested_dict())
    log(f"  ✅ 需求树已写入 traceability（{len(tree.nodes)} 行）")

    # ---- 落盘本阶段产物，供后续阶段消费 ----
    artifacts = work_dir / "reqcompile"
    artifacts.mkdir(parents=True, exist_ok=True)

    stages = {
        "tree.json": {
            "root_id": tree.root_id,
            "source_path": str(tree.source_path),
            "source_sha256": tree.source_sha256,
            "node_count": len(tree.nodes),
            "leaf_count": len(tree.leaves()),
            "nodes": [n.to_dict() for n in tree.ordered()],
        },
        "scenarios.json": index.to_dict(),
        "accessible_names.json": a11y.to_dict(),
        "validation.json": report.to_dict(),
    }
    for name, payload in stages.items():
        (artifacts / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    log(f"  ✅ 阶段产物 → {artifacts}")

    return {
        "node_count": len(tree.nodes),
        "leaf_count": len(tree.leaves()),
        "scenario_count": len(index.entries),
        "a11y_count": len(a11y.entries),
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "yaml_repairs": sum(len(r.repairs) for r in report.repairs),
    }, tree, a11y


# ---------- CLI ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="GOSIM 智能体软件工厂 · 自研薄管线",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("compile", help="从需求树编译出可运行的 Web 应用")
    p.add_argument("requirement_path", nargs="?",
                   help="requirements.yaml 或其所在目录")
    p.add_argument("-o", "--output-dir", default=None,
                   help="工作区/输出目录（也读 ARCBENCH_OUTPUT_DIR）")
    p.add_argument("--requirement-dir", default=None,
                   help="只给目录时的备用入口")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--stage", default="1",
                   help="跑到第几阶段（当前只实现 1）")

    sub.add_parser("doctor", help="环境体检")
    return parser


def cmd_doctor(args: argparse.Namespace) -> int:
    print("=== 环境体检 ===")
    print(f"  python      : {sys.version.split()[0]}")
    print(f"  cwd         : {Path.cwd()}")
    print(f"  本文件       : {Path(__file__).resolve()}")
    print(f"  sys.path[0] : {sys.path[0]}")
    print()
    print("=== ARCBENCH_* 环境变量（平台是否传了，看这里）===")
    found = False
    for k in sorted(os.environ):
        if k.startswith("ARCBENCH"):
            v = os.environ[k]
            print(f"  {k} = {v}")
            found = True
    if not found:
        print("  （一个都没有）")
    print()
    print("=== 平台注入的其它可能相关变量 ===")
    for k in ("PORT", "ARC_WEB_PORT", "ARC_TEST_DATE", "PWD", "HOME"):
        if k in os.environ:
            print(f"  {k} = {os.environ[k]}")
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    started = time.time()

    inputs = resolve_inputs(args)
    output_dir = inputs.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # 事件与 traceability 的落点交给 SDK——它自己会读 ARCBENCH_* 变量，
    # 并把相对路径解析到 project_dir 下（context.py:26-55）
    runtime = AgentRuntime.from_env(project_dir=str(output_dir))

    print("=" * 66)
    print("GOSIM 薄管线 · 第 1 阶段（需求编译）")
    print("=" * 66)
    print("参数来源：")
    for k, v in inputs.sources.items():
        print(f"  {k:18s} ← {v}")
    for n in inputs.notes:
        print(f"  ⚠️  {n}")
    print(f"  事件落点    : {runtime.paths.runner_events_path}")
    print(f"  traceability: {runtime.paths.traceability_dir}")
    print()

    runtime.events.mark_run_started("stage1: requirement compilation")
    exit_code = 0
    try:
        runtime.traceability.init_store()
        summary, tree, a11y = run_stage1(runtime, inputs, output_dir)

        # ---- 骨架落地（第 3 阶段的最小版本）----
        # 平台对每次 run 的第一道闸是"必须产出 frontend/ + backend/"：
        # 缺了它整轮 run 直接失败（实测报错 `web template is incomplete`），
        # 连"跑完"都拿不到——而"跑得完"是计分链条的第 1 环。详见 generate/scaffold.py。
        print()
        print("=" * 66)
        print("骨架落地（web 模板 → 输出目录）")
        print("=" * 66)
        from generate.scaffold import ScaffoldError, scaffold_app
        try:
            summary["scaffold"] = scaffold_app(output_dir)
        except ScaffoldError as exc:
            runtime.events.mark_run_failed(f"scaffold failed: {exc}")
            raise

        # ---- 实现生成（第 2/4 阶段的最小版本）----
        # 开关（环境变量，因为入口契约不许加 CLI 参数）：
        #   PIPELINE_GENERATE=0 → 跳过（做基础设施验证时用，不烧 token）
        #   PIPELINE_GENERATE=1 → 强制生成（没有 key 就报错）
        #   未设              → 有 key 就生成（平台路径无需额外配置）
        # PIPELINE_REQ_IDS=REQ-1,REQ-2 → 只生成这几个节点（省 token）
        mode = (os.environ.get("PIPELINE_GENERATE") or "").strip()
        summary["generation"] = run_generation(
            tree, a11y, output_dir, runtime=runtime, mode=mode,
            req_ids=[s for s in (os.environ.get("PIPELINE_REQ_IDS") or "").replace("，", ",").split(",") if s.strip()],
        )

        elapsed = time.time() - started
        print()
        print("-" * 66)
        print(f"阶段完成，用时 {elapsed:.1f}s   {summary}")
        print("-" * 66)

        if summary["error_count"]:
            runtime.events.mark_run_failed(
                f"stage1 finished with {summary['error_count']} validation errors"
            )
        else:
            runtime.events.mark_run_completed("stage1: requirement compilation")
    except Exception as exc:  # noqa: BLE001 —— 要把失败原因写进事件再抛
        runtime.events.mark_run_failed(f"stage1 crashed: {type(exc).__name__}: {exc}")
        raise
    return exit_code


def run_generation(tree, a11y, output_dir, *, runtime, mode: str, req_ids: list[str]) -> dict:
    """决定要不要生成、跑生成、把结果（含 token 用量）写进 run 事件与 stdout。"""
    from generate.implement import GenerationError, generate_app
    from generate.llm import LLMConfig, LLMError

    if mode == "0":
        print()
        print("实现生成：已按 PIPELINE_GENERATE=0 跳过（只做需求编译 + 骨架落地）")
        return {"skipped": True, "reason": "PIPELINE_GENERATE=0"}

    cfg = LLMConfig.from_env(output_dir=output_dir)
    if mode == "1":
        cfg.require_key()
    elif not cfg.api_key:
        print()
        print("实现生成：跳过（没有 API key，且未显式要求 PIPELINE_GENERATE=1）")
        return {"skipped": True, "reason": "no-api-key"}

    print()
    print("=" * 66)
    print(f"实现生成（模型 {cfg.model}，需求范围 {req_ids or '全部有场景的叶子'}）")
    print("=" * 66)
    try:
        result = generate_app(tree, a11y, output_dir, cfg, req_ids=req_ids or None)
    except (GenerationError, LLMError) as exc:
        runtime.events.mark_run_failed(f"generation failed: {exc}")
        raise

    # ---- 验证闭环：L1 闸门 → 模型自检 → 定向修复 → 复验（有界轮次）----
    # 开关：PIPELINE_VERIFY=0 跳过；PIPELINE_REPAIR_ROUNDS 轮次（默认 2）
    if (os.environ.get("PIPELINE_VERIFY") or "").strip() != "0":
        from generate.implement import build_requirement_brief
        from generate.scaffold import find_template_root, TEMPLATE_ID
        from verify.loop import verify_loop

        brief = build_requirement_brief(tree, a11y, req_ids or None)
        wanted = set(req_ids) if req_ids else None
        required = [e.name for e in a11y.entries if (wanted is None or e.req_id in wanted)]
        root = find_template_root()
        template_dir = (root / TEMPLATE_ID) if root else output_dir  # 找不到就当"无需比对"
        rounds = int((os.environ.get("PIPELINE_REPAIR_ROUNDS") or "3").strip() or 3)

        print()
        print("=" * 66)
        print(f"验证闭环（L1 + 自检 + 定向修复，轮次 {rounds}，可访问名靶子 {len(required)} 个）")
        print("=" * 66)
        result["verify"] = verify_loop(
            output_dir, requirement_brief=brief, required_names=required,
            template_dir=template_dir, cfg=cfg, rounds=rounds, log=print,
        )
    return result


def main() -> None:
    parser = build_parser()
    argv = sys.argv[1:]
    # 与 ARC 同款的兼容垫片：没写子命令就当 compile
    # （平台很可能按 `python main.py <需求路径> -o <目录>` 调）
    if argv and argv[0] not in {"compile", "doctor", "-h", "--help"}:
        argv = ["compile", *argv]
    if not argv:
        argv = ["compile"]

    args = parser.parse_args(argv)
    if args.command == "doctor":
        sys.exit(cmd_doctor(args))
    if args.command == "compile":
        sys.exit(cmd_compile(args))
    parser.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
