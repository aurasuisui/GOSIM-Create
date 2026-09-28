"""定向修复：按静态检查的 error（**以及构建报错**）逐文件重生成，带"改坏就回退"的安全网。

用法：python g2/tools/repair.py <产物目录> [需求包目录]

三条来自实测的约束：
  ① **一轮改不动**：模型会把同样的错路径再写一遍 → 跑两轮；
  ② **不能把对的改坏**：一轮修复曾把页面写成语法错 → 前端构建失败 → 整轮判分作废；
     所以改动前留备份，改完**跑构建**，不过就整轮回退；
  ③ **构建报错本身可修复**：esbuild 会给出 `文件:行:列: ERROR: …`，
     把它当成一条 finding 再修一次，比白丢一整轮便宜得多。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "g2")
sys.path.insert(0, "g2/tools")

from app.gen.design import Blueprint            # noqa: E402
from app.gen.llm import LLMConfig, Ledger       # noqa: E402
from app.gen.writer import FileJob, write_file  # noqa: E402
from app.reqcomp.loader import load_pack        # noqa: E402
from app.reqcomp.planner import group_requirements  # noqa: E402
from app.verify import staticcheck              # noqa: E402
from buildcheck import broken_files, build_ok, frontend_build  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "g2/work/bs4")
pack_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("g2/packs/bookstack")
tree = load_pack(pack_dir)
groups = group_requirements(tree)
bp = Blueprint(**{k: v for k, v in
                  json.loads((out / ".arc" / "g2-blueprint.json").read_text(encoding="utf-8")).items()
                  if k in {"routes", "api_endpoints", "db_tables", "auth", "error_shape", "notes"}})
cfg = LLMConfig.from_env(output_dir=out)
ledger = Ledger(out)


def kind_of(rel: str) -> str:
    if "/routes/" in rel:
        return "api-router"
    if "/pages/" in rel:
        return "page"
    return "app-shell"


for attempt in range(1, 3):
    rep = staticcheck.check(out, tree)
    bad = [f for f in rep["findings"] if f["severity"] == "error"]
    targets: dict[str, str] = {}
    for f in bad:
        p = Path(f.get("file", ""))
        if not p.name:
            continue
        rel = p.relative_to(out).as_posix() if out in p.parents else p.name
        targets[rel] = f["message"]

    # 构建（前端）失败的文件也算 finding —— 这一步在依赖装好之后才有意义
    built, build_out = frontend_build(out)
    extra: dict[str, str] = {}
    if not built:
        extra = broken_files(out, build_out)
        for rel, msg in extra.items():
            targets.setdefault(rel, "构建错误：" + msg)

    if not targets:
        print(f"pass {attempt}: 没有需要修复的 error（构建 {"ok" if built else "失败但无法定位文件"}）")
        break

    print(f"pass {attempt}: {len(targets)} 个文件待修 -> " + ", ".join(sorted(targets)[:6]))
    jobs = [FileJob(path=rel, kind=kind_of(rel), purpose="修复：" + msg[:130], req_ids=[])
            for rel, msg in targets.items()]

    backups: dict[Path, bytes] = {}
    for job in jobs:
        tgt = out / job.path
        if tgt.is_file():
            backups[tgt] = tgt.read_bytes()

    for job in jobs:
        try:
            r = write_file(cfg, job, bp, groups, out, existing=[j.path for j in jobs], ledger=ledger)
            print("   ", r.get("path"), r.get("chars"))
        except Exception as exc:  # noqa: BLE001
            print("    ❌ 修复失败:", job.path, str(exc)[:120])

    ok, why = build_ok(out)
    if not ok:
        print("  ⚠️ 修复后构建不过 → 整轮回退：", why.replace(chr(10), " ")[:200])
        for tgt, data in backups.items():
            tgt.write_bytes(data)
        ok2, why2 = build_ok(out)
        print("  after revert:", "build ok" if ok2 else ("still broken: " + why2[:120]))
        break

print("after:", json.dumps(staticcheck.check(out, tree)["summary"], ensure_ascii=False))