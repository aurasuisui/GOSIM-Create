# runs/ —— 实验记录

每个实验 arm 一条记录。**没有记录在案的实验等于没做过**——下一个会话无法判断某个改动是保留还是回退。

---

> 🔴 **2026-09-22 起：`git_commit` 字段要打折看。**
> 那天把头三天的历史**按"工作轮次"重排**过一次（72 → 23 个 commit），所以
> **2026-09-22 重排之前落盘的 RunRecord，其 `git_commit` 指向的是"重排前"的提交——那些提交已不在仓库里。**
> → **复核时以 `docs/`、本目录的 JSON 内容、以及 `STATUS.md` 为准**；
> 需要原始历史时用本地备份（`git bundle`，不在仓库内）。
> **2026-09-22 之后的记录不受影响**（新规则要求"跑测量前先提交干净"，`git_dirty` 应为 `false`）。

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

---

## 2026-09-22：keep 主线（子集 2→4 条需求 / 判分重复性 / 闭环 ROI）

**这条线的口径**：`keep` 官方档 `deepseek-chat`、`ARC_TEST_DATE=2026-09-20`、判分前清库。
`PIPELINE_REQ_IDS` 决定子集；**通过数必须连"通过的是哪一条"一起读**（子集外的偶然通过不算能力证据）。

| arm | 配置 / 这一轮在测什么 | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `r3a-1` | 子集 `{REQ-2.1, REQ-2.2}`、闭环 ON —— 判分**重复性**第 1 次 | —（复用产物） | **1 / 32** | `REQ-2.1.spec.ts:7`（`ok`） | `runs/20260922T031033Z-keep-score-r3a-1.json` |
| `r3a-2` | **同一产物、同一配置**的重复判分（第 2 次） | — | **1 / 32** | 同上，**逐条一致** → 翻面 ≈ 0（n=2） | `runs/20260922T040833Z-keep-score-r3a-2.json` |
| `e1-keep4` | 子集扩到 4 条 `{REQ-1.1, REQ-2.1, REQ-2.2, REQ-6.2}` —— **0 分那轮**（Express 4 的 `app.get('*')` 撞 Express 5 → 后端起不来 → 冒烟挂） | 35,555 | **判据未跑**（`tests_total=0`：冒烟失败，按纪律不跑判据） | — | `runs/20260922T050002Z-keep-score-e1-keep4.json` |
| `e1b-keep4` | 同上 4 条子集，修掉 Express 5 路由后**重生成** | 37,476 | **1 / 32** | **`REQ-2.7.3.spec.ts:7`**（子集**外**）→ "分数搬家" | `runs/20260922T050459Z-keep-score-e1b-keep4.json` |
| `e2-keep2` | **隔离变量**：与 `e1b-keep4` 同一份代码、子集退回 2 条 → 判断"扩子集掀掉 `REQ-2.1`"是否由请求压缩引起 | 28,788 | **1 / 32** | **`REQ-2.1.spec.ts:7`**（408ms）→ **压缩无辜**，真凶是"外壳单文件 + 修复预算单文件"的结构 | `runs/20260922T062208Z-keep-score-e2-keep2.json` |
| `r3b-on` | **闭环 ROI**：同一子集、同一产物序列，`PIPELINE_VERIFY=1`（闭环开） | **29,213**（11 次调用） | **1 / 32** | **`REQ-2.1.spec.ts:7`（443ms 通过）** | `runs/20260922T065715Z-keep-score-r3b-on.json` |
| `r3b-off` | 同上，`PIPELINE_VERIFY=0`（闭环闭）—— pre-registered：挂则闭环就是那 1 分的来源 | **14,316**（6 次调用） | **0 / 32** | 通过集合为空；**`REQ-2.1` 在 32 条失败里（11.1s 超时）** | `runs/20260922T073128Z-keep-score-r3b-off.json` |

**R3b 配对读数（2026-09-22 定稿）**：闭环 +104% token 换回**成员翻面**的那 1 分
（`REQ-2.1`：on 里 443ms 通过 → off 里 0/32 中的一条失败）。
⚠️ **判读口径**：条数差只有 1 条，低于本项目的可辨差异下限 2（硬规则 5）——
这个结论站得住**不是因为"1 比 0"**，而是因为 ① 失败形态不同（443ms vs 11.1s 超时）②
**独立静态证据**：种子字面量 `Sprint goals`/`Groceries` 在 on 家族产物里在、在 off 家族里**全无**
（机器可复现：`bash eval/regress_l1.sh` 的 `seed_literals` 列 = `m2-keep2 0 / e2-keep2 0 / e1b-keep4 2 / r3b-off 2`）。

**这批记录的已知瑕疵（复核时要打折的地方）**：

1. `r3a-1` 的 `arc_test_date` 是 `None`——该字段的读取是之后才修进 `extract_run.py` 的；
   同一轮日志里写着 `ARC_TEST_DATE=2026-09-20`。**别把"没记"当成"没设"**。
2. `r3a-2` 与 `r3b-off` 的 `git_dirty = true`（跑之前工作区没提交干净）→
   这两条读数对应的代码版本**无法从 `git_commit` 精确复原**。其余四份是 `dirty=false`。
3. `log_path` 指向的 `.judges.log` / `.log` **不随 git 走**（`runs/` 只跟 `*.json`）——
   clone 下来只能看 JSON，看不到原始输出；`per_test_passed` 已回写进 JSON（2026-09-22 补齐），
   所以"通过的是哪条"这条结论**不依赖**那些日志。

---

## 2026-09-22 追加：预注册复跑 `r4-keep4`（3a+3b 生效）

**它回答的问题**：3a（repair 契约优先级）+ 3b（种子字面量**判死**）能不能把 `REQ-2.1` 拿回来。
预注册（跑之前落盘）：`%TEMP%/r4-keep4/PREREG.md`。基线 = `e1b-keep4`（**1/32，通过的是子集外的 `REQ-2.7.3`**）。

| arm | 配置 | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `r4-keep4`（生成） | 4 条子集 `{REQ-1.1,REQ-2.1,REQ-2.2,REQ-6.2}` + 3a/3b 生效、闭环 ON | **37,290** | — | — | 生成日志 `%TEMP%/r4-keep4/gen.log`（不入库，见上第 3 条） |
| `r4-keep4b`（判分） | 同一产物，官方档、静置 | —（复用产物） | **2 / 32**（29.0 分钟） | **`REQ-2.1.spec.ts:7`（812ms）** + `REQ-2.7.3.spec.ts:7`（736ms） | `runs/20260922T090406Z-keep-score-r4-keep4b.json` |
| ~~`…r4-keep4`（第一次判分）~~ | 同一产物 | — | **17/32 中断**（MSYS fork 失败） | 中断产物**不计入任何通过数**（`REQ-2.1` 428ms 只作形态旁证） | `runs/20260922T083438Z-keep-score-r4-keep4.INVALID-interrupted.{log,judges.log}` |

**判读（按预注册第 ① 格）**：`REQ-2.1` **通过** 且 **种子字面量补上** ⇒ **3a+3b 生效**。
⚠️ **不要把结论建在"1 → 2"上**（差 1 条 < 可辨差异下限 2）：它靠 ① **成员翻面**
（`REQ-2.1` 由挂转过，且过在 812ms 的"通过形态"）② **机制链**（3b 第 1 轮硬判报缺 → 修复 → 第 2 轮 0 条 →
产物里字面量真的在，第三只眼 `grep` 复核）。

**已知瑕疵（两条，都已在 `docs/12` §十九.3 写明）**：
`r4-keep4b` 的 `load_snapshot` 记成了"正在跑"（**持锁时的自查**，工具 bug，已修；实际启动条件是 🟢 空闲）；
`git_dirty=true` 但**差别只在文档/记录**（`git diff b3c497e --stat -- pipeline/` 为空 → 产物对应的代码干净）。


