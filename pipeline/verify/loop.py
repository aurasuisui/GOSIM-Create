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
from verify.l1 import run_l1
from verify.selfcheck import repair, review


def verify_loop(output_dir: Path, *, requirement_brief: str, required_names: list[str],
                template_dir: Path, cfg: LLMConfig, rounds: int = 2,
                max_files: int = 4, log=print) -> dict:
    """跑验证闭环，返回全过程记录（给 RunRecord / 文档用）。"""
    history: list[dict] = []

    for rnd in range(1, rounds + 1):
        log("")
        log(f"=== 验证闭环 第 {rnd}/{rounds} 轮 ===")
        l1 = run_l1(output_dir, requirement_brief=requirement_brief,
                    required_names=required_names, template_dir=template_dir, log=log)

        # 每轮都做一次模型自检：L1 是静态的，抓不到"对不上需求"那类（M3a 的 5.3）
        violations = review(output_dir, requirement_brief, l1["findings"], cfg, log=log)
        log(f"  自检发现 {len(violations)} 条对不上需求的缺陷")
        for v in violations:
            log(f"    · {v['file']}: {v['problem'][:90]}")

        # 把 L1 的机械发现也转成同一种"违规"形状，一起修
        mech = [{"file": f["file"], "problem": f["detail"], "fix": f["hint"]}
                for f in l1["findings"]
                if not f["file"].endswith("package.json") and "/database/" not in f["file"]]
        # 依赖同名文件的问题合并（同一次调用里一起修）
        all_v = mech + violations

        history.append({"round": rnd, "l1_passed": l1["passed"],
                        "l1_per_check": l1["per_check"],
                        "l1_findings": l1["findings"], "review_violations": violations})

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

    final = run_l1(output_dir, requirement_brief=requirement_brief,
                   required_names=required_names, template_dir=template_dir, log=log)
    log(f"  闭环结束：L1 {'✅ 通过' if final['passed'] else '❌ 仍有发现'}；"
        f"累计 token {totals(cfg)['total_tokens']}")
    return {"history": history, "final_l1": final, "usage": totals(cfg), "calls": list(cfg.calls)}
