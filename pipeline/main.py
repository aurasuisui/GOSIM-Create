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


# ---------- 凭据体检（掩码，绝不打印值）----------

def log_model_env(output_dir: Path) -> None:
    """打一行**掩码**凭据检查：模型的来源、base_url、key 的存在性与长度。

    为什么必须有（`PLAN.md` §7 M3b-1 的 A1）：整条链上**只有这一个假设是"为假就全盘归零"**——
    平台注入不注入 `MODEL`、有没有可用的 key、网关认不认。而**"平台不注入 key"至今没有定论**
    （V5 只看了 stdio，没确认环境里 key 的存在性）。平台上的日志是我们唯一能看见的东西，
    所以每次 run 都要把这三件事的**存在性**写进日志——**值一个都不打印**。

    判读：`key: absent` 而 `model: deepseek-v4-flash` → 生成会跳过（`run_generation` 说明原因），
    这一轮不可能有功能；`base_url` 不是比赛网关 → 说明平台改了口径。
    """
    from generate.llm import LLMConfig      # 延迟 import，与 run_generation 保持一致
    cfg = LLMConfig.from_env(output_dir=output_dir)
    _os = os

    def mask(s: str) -> str:
        # **只报长度，不报任何字符**——前缀也算值的一部分，日志会被平台留存
        return f"set(len={len(s)})" if s else "absent"

    src_model = next((n for n in ("PIPELINE_LLM_MODEL", "MODEL", "ARC_MODEL")
                      if (_os.environ.get(n) or "").strip()), None)
    print("=== 凭据体检（掩码；只报存在性与长度，不打印值）===")
    print(f"  model    : {cfg.model or '(空)'}"
          f"   ← {'环境变量 ' + src_model if src_model else '默认/工作区 .env'}")
    print(f"  base_url : {cfg.base_url or '(空)'}")
    print(f"  key      : {mask(cfg.api_key)}"
          f"   ← {'环境变量' if _os.environ.get('PIPELINE_LLM_API_KEY') or _os.environ.get('OPENAI_API_KEY') or _os.environ.get('ARC_API_KEY') else '工作区 .env（平台路径上不会有）'}")
    print(f"  VISUAL_MODEL : {_os.environ.get('VISUAL_MODEL') or 'absent'}（本管线暂不用视觉）")
    print(f"  ARCBENCH_OUTPUT_DIR : {_os.environ.get('ARCBENCH_OUTPUT_DIR') or 'absent'}")
    print(f"  ARCBENCH_RUNNER_EVENTS_PATH : {_os.environ.get('ARCBENCH_RUNNER_EVENTS_PATH') or 'absent'}")

    # ---- 本轮knobs：**回显每一开关的实际取值**（第二十一轮审核 §一.11）----
    # 为什么必须回显：此前只能靠"日志里有没有『验证闭环』那行"反推闭环开关——
    # **absence 会被读成"没开"**，而它也可能是"这行没打印"或"日志被截了"。
    # 这是本项目的"判据没报错 ≠ 判据跑过了"的同型 （实测过一次：三次跑里判据①静默失败而都"看起来正常"）。
    req_ids_env = (_os.environ.get("PIPELINE_REQ_IDS") or "").strip()
    verify_env = (_os.environ.get("PIPELINE_VERIFY") or "").strip()
    rounds_env = (_os.environ.get("PIPELINE_REPAIR_ROUNDS") or "").strip()
    print("=== 本轮 knobs（回显实际取值，别再从日志的 absence 反推）===")
    print(f"  PIPELINE_REQ_IDS      : {req_ids_env or 'absent'}（缩子集；absent = 全量）")
    print(f"  PIPELINE_VERIFY       : {verify_env or 'absent'}（absent 视为开；=0 关闭闭环）")
    print(f"  PIPELINE_REPAIR_ROUNDS: {rounds_env or 'absent'}（absent = 默认 3 → 实际只有 rounds-1 次修复）")
    print(f"  PIPELINE_DESIGN_ONLY  : {(_os.environ.get('PIPELINE_DESIGN_ONLY') or '').strip() or 'absent'}"
          "（=1 只跑设计、不生成，省 token）")
    # 预算公式的常数**从代码里读**，不写死在文案里（写死就会与代码漂移 = "工具与文档不一致"）
    from generate.implement import BUDGET_BASE, BUDGET_MAX, BUDGET_MIN, BUDGET_PER_CHAR
    budget_env = (_os.environ.get("PIPELINE_TOKEN_BUDGET") or "").strip()
    print(f"  token 预算            : {budget_env or '（未设 → 按 brief 体量推导）'}"
          f"   公式={BUDGET_BASE:,} + {BUDGET_PER_CHAR}×brief字符，"
          f"clamp[{BUDGET_MIN:,}, {BUDGET_MAX:,}]")
    if not cfg.api_key:
        print("  ⚠️  没有 key：实现生成会被跳过（只有骨架落地）——这一轮不会有功能，"
              "但**仍会产出 frontend/ + backend/**（平台第一道闸能过）。")
    print()


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
    log_model_env(output_dir)

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

        # ---- 产物指纹（**出生处**盖，不是写记录时现采）----
        # 第二十三轮审核 §三 的根因：记录里的 `pipeline.git_*` 原本在判分跑完（约一小时后）
        # 才求值 → attest 的是那一刻的工作区，不是这份产物。写法与理由见 `provenance.py`。
        from provenance import write as _write_provenance
        _req_ids = [s for s in (os.environ.get("PIPELINE_REQ_IDS") or "")
                    .replace("，", ",").split(",") if s.strip()]
        _prov_extra = {
            "app_hint": _app_hint(tree, output_dir),
            "req_ids": _req_ids,
            "generate_mode": (os.environ.get("PIPELINE_GENERATE") or "").strip(),
        }
        summary["provenance"] = _write_provenance(
            output_dir, requirement_path=getattr(tree, "source_path", None), extra=_prov_extra)

        # ---- 实现生成（第 2/4 阶段的最小版本）----
        # 开关（环境变量，因为入口契约不许加 CLI 参数）：
        #   PIPELINE_GENERATE=0 → 跳过（做基础设施验证时用，不烧 token）
        #   PIPELINE_GENERATE=1 → 强制生成（没有 key 就报错）
        #   未设              → 有 key 就生成（平台路径无需额外配置）
        # PIPELINE_REQ_IDS=REQ-1,REQ-2 → 只生成这几个节点（省 token）
        mode = (os.environ.get("PIPELINE_GENERATE") or "").strip()
        summary["generation"] = run_generation(
            tree, a11y, output_dir, runtime=runtime, mode=mode,
            req_ids=_req_ids,
        )

        elapsed = time.time() - started
        # 指纹补第二次（把"跑完"这个事实也写进去；最后一次调用生效）
        # ⚠️ **必须等闭环跑完再盖**（第二十四轮审核 §三 A）：`run_generation` 内部才跑验证闭环，
        # 闭环绕的 token 占全程 **51–58%**（闭环自己那轮实测的核心量，也是 pass/CNY 的分母），
        # 早盖会把 `tokens_total` 写成"只有生成那一段"（实测 15,833 = 全程 42%）。
        # 两个数**分开命名**，别让一个字段同时表示两件事：
        #   tokens_generation_only = 生成段（设计 + 各块实现）
        #   tokens_total           = 全程（生成段 + 验证闭环）——**没有闭环时两者相等**
        _gen = (summary.get("generation") or {})
        _gen_tok = (_gen.get("usage") or {}).get("total_tokens")
        _ver = (_gen.get("verify") or {})
        _ver_tok = (_ver.get("usage") or {}).get("total_tokens")
        _write_provenance(output_dir, requirement_path=getattr(tree, "source_path", None),
                          extra={**_prov_extra,
                                 "stage_seconds": round(elapsed, 1),
                                 "tokens_generation_only": _gen_tok,
                                 "tokens_total": _ver_tok if _ver_tok is not None else _gen_tok,
                                 "verify_ran": bool(_ver) and not _ver.get("skipped")},
                          log=lambda *a, **k: None)
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


def _app_hint(tree, output_dir: Path) -> str:
    """app 名：需求树的源路径末段（例如 `…/webapp/<app>` → `<app>`）。

    只用于**记录/日志/指纹**（`provenance.json` 的 `app_hint`）——
    **token 预算已改成按 brief 体量推导**（`generate.implement.token_budget`），不再按 app 名查表。

    拿不到就返回空串 → 走默认预算（`PLAN.md` §4.3 的"每个阶段显式预算"）。
    """
    # 需求路径的形态不止一种：`…/webapp/<app>/requirements/requirements.yaml`、
    # `…/example/ticketbooking-quickstart/requirements.yaml`、平台输出的临时目录……
    # 所以**从路径往上走，跳过已知的非 app 段**，取第一个剩下的名字。
    skip = {".", "webapp", "example", "template", "app", "requirements", "requirement"}
    for cand in (getattr(tree, "source_path", None), output_dir):
        if not cand:
            continue
        parts = [Path(str(cand)).name] + [pp.name for pp in Path(str(cand)).parents]
        for part in parts:
            if not part or part.startswith(".") or part in skip:
                continue
            if part.endswith((".yaml", ".yml", ".json")):
                continue
            return part
    return ""


def run_generation(tree, a11y, output_dir, *, runtime, mode: str, req_ids: list[str]) -> dict:
    """决定要不要生成、跑生成、把结果（含 token 用量）写进 run 事件与 stdout。"""
    from generate.implement import GenerationError, generate_app
    from generate.llm import LLMConfig, LLMError
    from generate.schema import SchemaInjectionError

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
        # app 名用于记录/指纹；需求路径的末段就是它（…/webapp/<app>）
        app_hint = _app_hint(tree, output_dir)
        result = generate_app(tree, a11y, output_dir, cfg, req_ids=req_ids or None,
                              app_hint=app_hint)
    except (GenerationError, LLMError, SchemaInjectionError) as exc:
        runtime.events.mark_run_failed(f"generation failed: {exc}")
        raise

    # ---- 到额降级：预算已花完就不再进闭环（闭环是"打磨"，不决定有没有分）----
    # 预算**由生成阶段按 brief 体量算好**并回传（`generate.implement.token_budget`）——
    # 这里不重新按 app 名查表（那张表是题目特定字符串，已删）
    budget = result.get("token_budget") or 0
    spent = (result.get("usage") or {}).get("total_tokens") or 0
    if spent >= budget:
        print()
        print(f"⚠️  已用 {spent:,} ≥ 预算 {budget:,}：**跳过验证闭环**（到点就收）")
        result["verify"] = {"skipped": True, "reason": "token-budget-exhausted",
                            "spent": spent, "budget": budget}
        return result

    # ---- 验证闭环：L1 闸门 → 模型自检 → 定向修复 → 复验（有界轮次）----
    # 开关：PIPELINE_VERIFY=0 跳过；PIPELINE_REPAIR_ROUNDS 轮次（默认 2）
    if (os.environ.get("PIPELINE_VERIFY") or "").strip() != "0":
        from generate.implement import build_requirement_brief
        from generate.scaffold import find_template_root, TEMPLATE_ID
        from verify.loop import verify_loop

        # ② 对表补进来的硬名（**操作者**通过环境变量传；不写进仓库，见 AGENTS 红线 9）
        extra_hard = [s.strip() for s in (os.environ.get("PIPELINE_EXTRA_HARD_NAMES") or "")
                      .replace("，", ",").split(",") if s.strip()]
        if extra_hard:
            print(f"  补充硬名（来自 ② 的靶子闭包对表）：{extra_hard}")
        brief = build_requirement_brief(tree, a11y, req_ids or None, extra_hard=extra_hard)
        wanted = set(req_ids) if req_ids else None
        # 两类靶子分开：引号式/规则式 = **必需**（精确名判、驱动修复）；
        # 散文式（§4.1 第 5 条）= **fail-soft**（只告警、不驱动修复，见 verify/l1.py）
        picked = [e for e in a11y.entries if (wanted is None or e.req_id in wanted)]
        # 种子数据字面量（PLAN §7 的 3b）：**只取本次子集里的需求**——
        # 不限范围就会把"子集外需求的种子"也报成缺失（实测：2 条需求的产物被报缺 20 条，
        # 而这 20 条**全部**属于没生成的需求 → 那就是"又宽又松"）。
        from reqcompile.prose import extract_seed_literals
        seed_literals: list[str] = []
        for n in tree.ordered():
            if not (n.is_leaf and n.scenarios):
                continue
            if wanted is not None and n.id not in wanted:
                continue
            txt = (n.description or "") + " " + " ".join(
                (st.content or "") for sc in n.scenarios for st in sc.steps)
            for s in extract_seed_literals(txt):
                if s not in seed_literals:
                    seed_literals.append(s)
        print(f"  种子数据字面量（本子集）：{len(seed_literals)} 条")
        # 补充硬名也进"契约"（L1 的 check_accessible_names + 3a 的 priority 0）——
        # 否则它只是 prompt 里的一句话，"发现没兑现"就没人管
        required = [e.name for e in picked
                    if e.name and not (e.pattern or "").startswith("prose")] + extra_hard
        soft = [e.name for e in picked
                if e.name and (e.pattern or "").startswith("prose")]
        root = find_template_root()
        template_dir = (root / TEMPLATE_ID) if root else output_dir  # 找不到就当"无需比对"
        rounds = int((os.environ.get("PIPELINE_REPAIR_ROUNDS") or "3").strip() or 3)

        print()
        print("=" * 66)
        print(f"验证闭环（L1 + 自检 + 定向修复，轮次 {rounds}，可访问名靶子 {len(required)} 个）")
        print("=" * 66)
        result["verify"] = verify_loop(
            output_dir, requirement_brief=brief, required_names=required,
            soft_names=soft, seed_literals=seed_literals,
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
