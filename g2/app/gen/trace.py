"""把产物登记进 ARC-Bench 的**可追溯性存储**（`.arc/traceability/*.json`）+ 运行事件。

为什么要做（2026-09-28 读官方模板后的发现）：
  平台提供的 agent 模板里带一个 runtime SDK，明确要求 agent 把
  **需求 / 场景 / 接口 / 测试 / 节点状态**登记到 `.arc/traceability/`，并往
  `runner-events.jsonl` 写状态事件（"so the ARC Bench frontend can display
  traceability changes"）。榜单上除了**通过率**还有一个 **覆盖度** —— 那大概率就来这里。
  我此前的管线**一个字节都没写**，等于把这一块全丢了。

格式（照 `arcbench-agent-runtime` 的 `TABLE_NAMES` 与 events.py 抄的，零依赖、零 token）：
  - 每张表一个 JSON 对象：`requirements/scenarios/interfaces/tests/call_edges/node_states/node_contracts`；
  - 事件一行一个 JSON：{"type":"requirement_state","node_id":...,"phase":...,"status":...,"timestamp":...}；
  - 路径优先用平台注入的 `ARCBENCH_TRACEABILITY_DIR` / `ARCBENCH_RUNNER_EVENTS_PATH`。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

TABLE_NAMES = ("requirements", "scenarios", "interfaces", "tests", "call_edges",
               "node_states", "node_contracts")


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _slug(text: str, limit: int = 32) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-")
    return (s or "Item")[:limit]

def write_traceability(out_dir: Path, tree, blueprint, *, tests_passed: dict[str, bool] | None = None,
                       log=print) -> dict:
    """登记需求树 + 生成物 + 节点状态，并写运行事件。返回统计（用于日志/报告）。"""
    out_dir = Path(out_dir)
    root = Path(os.environ.get("ARCBENCH_TRACEABILITY_DIR") or (out_dir / ".arc" / "traceability"))
    events_path = Path(os.environ.get("ARCBENCH_RUNNER_EVENTS_PATH")
                       or (out_dir / ".arc" / "runner-events.jsonl"))
    root.mkdir(parents=True, exist_ok=True)
    events_path.parent.mkdir(parents=True, exist_ok=True)

    tables: dict[str, dict] = {name: {} for name in TABLE_NAMES}

    # ---- 需求与场景（来自需求树本身）----
    req_ids: list[str] = []
    for node in tree.all_nodes():
        nid = str(getattr(node, "id", "") or "").strip()
        if not nid:
            continue
        req_ids.append(nid)
        # ⚠️ 字段要对齐**官方 SDK**（`arcbench_agent_runtime.traceability`）：
        #    它每条记录都带 `updated_at`，requirements 还带 `visual_reference`。
        #    我第一版只写了"必需字段" → 平台侧节点状态仍显示 design（实测），
        #    而 `feature_implementation_rate` 一直是 0.0 —— 格式不齐很可能就是原因。
        tables["requirements"][nid] = {
            "req_id": nid,
            "updated_at": _ts(),
            "visual_reference": [],
            "name": str(getattr(node, "title", "") or getattr(node, "name", "") or nid),
            "description": str(getattr(node, "description", "") or "")[:2000],
            "parent_id": str(getattr(node, "parent_id", "") or "") or None,
        }
        for si, sc in enumerate(getattr(node, "scenarios", []) or [], 1):
            sc_id = str(getattr(sc, "id", "") or f"{nid}-SCN-{si}")
            steps = [{"keyword": str(getattr(st, "keyword", "") or ""),
                      "content": str(getattr(st, "content", "") or "")[:500]}
                     for st in (getattr(sc, "steps", []) or [])]
            tables["scenarios"][sc_id] = {"scenario_id": sc_id, "req_id": nid,
                                          "name": str(getattr(sc, "name", "") or sc_id),
                                          "updated_at": _ts(),
                                          "steps": steps}

    # ---- 接口：API 端点 + 前端页面（**实现即登记为 implemented**）----
    used_reqs = set()
    for ep in getattr(blueprint, "api_endpoints", []) or []:
        if not isinstance(ep, dict):
            continue
        path = str(ep.get("path") or ep.get("route") or "")
        method = str(ep.get("method") or "GET").upper()
        if not path:
            continue
        reqs = [str(r) for r in (ep.get("req_ids") or [])][:4]
        if not reqs:
            continue
        iid = f"{reqs[0]}.API.{_slug(path)}"
        fp = str(ep.get("file_path") or "") or None
        tables["interfaces"][iid] = {"interface_id": iid, "req_ids": reqs, "type": "api",
                                     "content": f"{method} {path}", "file_path": fp,
                                     "first_line": None, "updated_at": _ts(),
                                     "implemented": True}
        used_reqs.update(reqs)
    for route in getattr(blueprint, "routes", []) or []:
        if not isinstance(route, dict):
            continue
        path = str(route.get("path") or "")
        comp = str(route.get("component") or "")
        if not path or not comp:
            continue
        iid = f"UI.{_slug(comp)}"
        fp = f"frontend/src/pages/{comp}.tsx"
        if not (out_dir / fp).is_file():
            continue
        tables["interfaces"][iid] = {"interface_id": iid, "req_ids": [], "type": "ui",
                                     "content": f"{path} → {comp}", "file_path": fp,
                                     "first_line": None, "updated_at": _ts(),
                                     "implemented": True}

    # ---- 测试：平台不给测试文件，就按"每个有场景的需求"登记一条 E2E 引用 ----
    for nid, rec in list(tables["requirements"].items()):
        has_scn = any(s.get("req_id") == nid for s in tables["scenarios"].values())
        if not has_scn:
            continue
        tid = f"{nid}.E2E"
        passed = (tests_passed or {}).get(nid)
        tables["tests"][tid] = {"test_id": tid, "req_id": nid, "type": "E2E",
                                "passed": passed,
                                "file_path": None, "first_line": None, "scenario_id": None,
                                "updated_at": _ts(),
                                "interface_ids": [i for i, v in tables["interfaces"].items()
                                                  if nid in (v.get("req_ids") or [])][:4]}

    # ---- 节点状态 + 事件 ----
    events: list[dict] = []
    for nid in req_ids:
        implemented = nid in used_reqs
        state = "IMPLEMENTED" if implemented else "DESIGNED"
        # ★ 官方 SDK 的 node_states 一定带 `updated_at`（少了它，平台侧可能整条不认）
        tables["node_states"][nid] = {"req_id": nid, "state": state, "phase": "implement",
                                      "updated_at": _ts()}
        events.append({"type": "requirement_state", "node_id": nid, "phase": "design",
                       "status": "completed", "timestamp": _ts(), "message": None})
        if implemented:
            events.append({"type": "requirement_state", "node_id": nid, "phase": "implement",
                           "status": "completed", "timestamp": _ts(), "message": None})

    # 官方 SDK 还会写 `type:"signal"` 的**刷新事件**（前端据此拉新数据）。
    # 形态照 `references/actions.md`：traceability-changed → refresh 标志。
    events.append({"type": "signal", "reason": "traceability_store_initialized", "timestamp": _ts(),
                   "refresh": {"submission": True, "logs": False, "commit_history": False,
                               "traceability_selected": True, "traceability_all": True,
                               "preview": False}})
    events.append({"type": "signal", "reason": "requirements_updated", "timestamp": _ts(),
                   "refresh": {"submission": True, "logs": False, "commit_history": False,
                               "traceability_selected": True, "traceability_all": True,
                               "preview": False}})

    for name, rows in tables.items():
        (root / f"{name}.json").write_text(json.dumps(dict(sorted(rows.items())), ensure_ascii=False,
                                           indent=2), encoding="utf-8")
    with events_path.open("a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    stats = {"requirements": len(tables["requirements"]), "scenarios": len(tables["scenarios"]),
             "interfaces": len(tables["interfaces"]), "tests": len(tables["tests"]),
             "node_states": len(tables["node_states"]), "events": len(events)}
    log("  可追溯性: " + json.dumps(stats, ensure_ascii=False) + f"  → {root}")
    return stats


def ensure_git(out_dir: Path, log=print) -> bool:
    """尽力而为：把产物变成 git 仓库并提交一次（平台模板建议这么做，便于看实现历史）。"""
    out_dir = Path(out_dir)
    try:
        def run(*args: str) -> int:
            return subprocess.run(["git", *args], cwd=str(out_dir), capture_output=True,
                                  text=True, timeout=120).returncode
        if not (out_dir / ".git").is_dir():
            if run("init", "-q") != 0:
                return False
        run("config", "user.email", "agent@arc-bench.local")
        run("config", "user.name", "arc-bench-agent")
        run("add", "-A")
        run("commit", "-q", "-m", "agent: generate web app from requirements")
        log("  git: 已初始化并提交（便于平台查看实现历史）")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"  git: 跳过（{type(exc).__name__}）")
        return False