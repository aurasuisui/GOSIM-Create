# runs/ —— 实验记录

每个实验 arm 一条记录。**没有记录在案的实验等于没做过**——下一个会话无法判断某个改动是保留还是回退。

---

## 命名

```
runs/
├── 20260920T101500Z-12306-baseline.log        ← bench.sh 产出的原始输出
├── 20260920T103000Z-12306-arm-glm53.json      ← 结构化 RunRecord
└── .backend-12306.log                          ← 后端日志（临时，可删）
```

`<UTC时间戳>-<app>-<arm_id>.<ext>`

**例外（不是 RunRecord，不要用 `extract_run.py` 解析）**：

```
20260920T-a11y-recall-all-apps.txt     ← 六 app 可访问名召回抽检（收窄前的口径）
20260920T-interaction-unit-a11y.txt    ← 同上，当前口径（交互单元空转率）
```

它们是**离线测量**（不跑 bench、不占端口），复现命令 `python docs/06-a11y-recall-probe.py`，
结论在 `docs/06-需求契约实测.md` §三补 / §三补-2。放进 `runs/` 是为了让结论有原始证据可核。

---

## RunRecord 格式

**完整字段清单见 `docs/04-测量协议.md` §5。** 最小可用版本：

```json
{
  "run_id": "20260920T103000Z-12306-arm-glm53",
  "arm_id": "arm-glm53",
  "change_description": "把批量生成的模型从 deepseek-v4-flash 换成 glm-5.3-flash",
  "git_commit": "<pipeline 的 commit>",
  "git_dirty": false,

  "app": "12306",
  "bench_repo_commit": "<arc-bench 的 commit>",
  "requirements_sha256": "...",
  "arc_test_date": "2026-09-20",

  "model": "glm-5.3-flash",
  "visual_model": null,
  "port": 3301,

  "compile_status": "completed",
  "compile_wall_clock_s": 0,
  "nodes_total": 117,
  "nodes_failed": [],

  "frontend_build_ok": true,
  "backend_start_ok": true,
  "db_prepare_e2e_exit_code": 0,

  "tests_total": 135,
  "tests_passed": 128,
  "tests_failed": 7,
  "pass_rate": 0.948,
  "noise_flip_count": 4,
  "failure_class_counts": {
    "timeout": 4,
    "a11y_name_mismatch": 2,
    "seed_missing": 1
  },

  "input_tokens": 0,
  "output_tokens": 0,
  "total_tokens": 0,
  "cny_delta": 0.0,
  "pass_per_cny": null,

  "workspace_path": "...",
  "notes": ""
}
```

---

## 记录纪律

1. **跑之前先写 `change_description`**（一句话、可证伪）。事后补写会不由自主地往有利方向解释。
2. **核对 `noise_flip_count`**。差异在这个数以内的一律视为噪声，不要下结论。
3. **`pass_per_cny` 是主指标**，不是 `pass_rate`。
4. **失败 run 也要记**，且计入成本分母——赛制口径包含失败尝试的消耗。
5. 每个 arm 至少 3 轮（每轮前 `eval/bench.sh reset`）。要比较整跑成功率则需 ≥8–10 轮。

---

## 基线（首日实测，2026-09-20）

写入第一条正式记录时以此为参照：

| 指标 | 值 |
|---|---|
| 参考实现通过率（干净库，**8 轮**：R1–R5 + 静置 Q1–Q3） | 129 / 130 / 130 / 133 / 135 / **134 / 135 / 133**（均为 /135） |
| 参考实现通过率（脏库，未重置） | 120 / 135 |
| **实际上界** | **135 / 135 —— R5 实测达到过**（2026-09-20 12:33，`20260920T043334Z-12306-round1`） |
| 单轮耗时（12306） | 3.0 – 8.0 min（静置轮次都更快：3.0–3.4 min） |
| **噪声底线（条数口径）** | **只在静置条件下比较**。静置 0–2（跨度 2）→ 可辨差异 **2 条**、**保留门槛 4 条**；污染组（R1–R4）2–6（跨度 4）。混口径的「跨度 6」已作废 |
| **噪声底线（集合口径）** | 会翻面的测试共 **11 条**（R5 里全部通过）→ per-test 对比看**成员变化** |
| 失败形态 | 主要是 `toBeVisible()` / `toBeChecked()` 超时（交互时序，非功能缺失） |

> 权威数据与完整分析在 `docs/04-测量协议.md` §三。**不要写"132/132"——那是早期非工具化的一轮。**
>
> 📄 **静置序列的可读版记录**：`runs/20260920T-quiet-seq-README.md`
> （3 轮 + 交叉对比 + 已知瑕疵 + 复现命令；那是噪声底线的权威输入）。
>
> 🔴 **两条旧结论已被 R5 推翻**（2026-09-20 晚些时候）：
> ① "`REQ-4.3.7` 稳定失败"→ **它通过了，零稳定失败**；
> ② "理论天花板 134/135"→ **作废，实测区间 129–135**。
> "不追 100%" 的理由从"满分不存在"改成"满分不稳定"。

**跨 app 外推**：6 个 app 约 484 条测试，纯测试时间估 15–30 分钟，不含生成时间。
