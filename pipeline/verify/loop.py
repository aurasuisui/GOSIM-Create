"""验证闭环：L1 闸门 → 模型自检 → 定向修复 → L1 复验（**有界轮次**）。

这是 M3b-0 的机器化那一半：把 M3a 的四次手工修复换成"机器发现 → 机器修 → 机器复验"。

**为什么轮次是有界的（默认 2）**：`PLAN.md` §7 M3a 分叉表第二行明确写了
"**不要先加迭代轮次**"——大意是别用轮次掩盖能力问题。
这里的有界轮次不是为了"多试几次碰运气"，而是为了让**发现→修复**这条链路真的闭合：
每轮结束都要 L1 复验，改完还挂就停手，并**如实把剩余项报出来**（不假装成功）。

**成本护栏**：单轮最多修 `max_files` 个文件（默认 4），避免一次把 9 个文件全重写
（这个模型 reasoning 很贵，见 `docs/10` §六）。
"""
from __future__ import annotations

from pathlib import Path

from generate.llm import LLMConfig, totals
from generate.schema import SCHEMA_REL, inject_schema
from verify.l1 import run_l1
from verify.selfcheck import repair, review


def _reinject(output_dir: Path, *, log=print) -> None:
    """重跑建表落位。**失败不抛**——让 L1 把它报成一条发现，而不是让整轮生成炸掉。"""
    try:
        inject_schema(output_dir, log=log)
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠️  建表落位失败（{type(exc).__name__}: {exc}）——L1 会把它报出来")


def _mechanical_findings(findings: list[dict]) -> list[dict]:
    """L1 的机械发现 → 违规形状。两类要特殊处理：

    · `schema-not-injected` → **不叫模型**：修法是管线动作（重注入），改代码改不出来
    · `table-not-created`   → 把 file **路由到 `schema.sql`**：否则模型会跑到仓储模块里
      写 `CREATE TABLE`（能骗过静态检查，但落位不确定，正是 gate 0 挂掉的那个坑）
    """
    out: list[dict] = []
    for f in findings:
        if f["kind"] == "schema-not-injected":
            continue
        rel = SCHEMA_REL if f["kind"] == "table-not-created" else f["file"]
        if rel.endswith("package.json"):
            continue
        if rel.startswith("backend/src/database/") and rel != SCHEMA_REL:
            continue                       # 脚手架文件禁改（init_db.js 由管线自己管）
        out.append({"file": rel, "problem": f["detail"], "fix": f["hint"]})
    return out


def verify_loop(output_dir: Path, *, requirement_brief: str, required_names: list[str],
                template_dir: Path, cfg: LLMConfig, rounds: int = 2,
                max_files: int = 4, soft_names: list[str] | None = None,
                log=print) -> dict:
    """跑验证闭环，返回全过程记录（给 RunRecord / 文档用）。"""
    history: list[dict] = []

    for rnd in range(1, rounds + 1):
        log("")
        log(f"=== 验证闭环 第 {rnd}/{rounds} 轮 ===")
        l1 = run_l1(output_dir, requirement_brief=requirement_brief,
                    required_names=required_names, template_dir=template_dir,
                    soft_names=soft_names or [], log=log)

        # 注入块丢了/落后了 → 先做**管线自己的确定性修复**（0 token），再重新取证
        if any(f["kind"] == "schema-not-injected" for f in l1["findings"]):
            log("  🔧 机械修复：重跑建表落位（管线动作，0 token）")
            _reinject(output_dir, log=log)
            l1 = run_l1(output_dir, requirement_brief=requirement_brief,
                        required_names=required_names, template_dir=template_dir,
                        soft_names=soft_names or [], log=log)

        # 每轮都做一次模型自检：L1 是静态的，抓不到"对不上需求"那类（M3a 的 5.3）
        violations = review(output_dir, requirement_brief, l1["findings"], cfg, log=log)
        log(f"  自检发现 {len(violations)} 条对不上需求的缺陷")
        for v in violations:
            log(f"    · {v['file']}: {v['problem'][:90]}")

        # 把 L1 的机械发现也转成同一种"违规"形状，一起修
        mech = _mechanical_findings(l1["findings"])
        # 依赖同名文件的问题合并（同一次调用里一起修）
        all_v = mech + violations

        history.append({"round": rnd, "l1_passed": l1["passed"],
                        "l1_per_check": l1["per_check"],
                        "l1_findings": l1["findings"],
                        "l1_soft": l1.get("soft_findings", []),
                        "review_violations": violations})

        if not all_v:
            log("  ✅ 本轮无发现：L1 通过且自检无缺陷")
            break
        if rnd == rounds:
            log("  ⚠️ 已是最后一轮，不再修（剩余项如实记入 history）")
            break

        log(f"  进入定向修复（≤{max_files} 个文件）")
        written = repair(output_dir, all_v, cfg, log=log, max_files=max_files)
        history[-1]["repaired_files"] = written
        log(f"  本轮修了 {len(written)} 个文件：{', '.join(written) or '（无）'}")

        # schema.sql 可能刚被修过 → 重新落位，否则新表不会在启动时建
        if SCHEMA_REL in written:
            _reinject(output_dir, log=log)

    final = run_l1(output_dir, requirement_brief=requirement_brief,
                   required_names=required_names, template_dir=template_dir,
                   soft_names=soft_names or [], log=log)
    log(f"  闭环结束：L1 {'✅ 通过' if final['passed'] else '❌ 仍有发现'}；"
        f"累计 token {totals(cfg)['total_tokens']}")
    return {"history": history, "final_l1": final, "usage": totals(cfg), "calls": list(cfg.calls)}
