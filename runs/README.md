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

## 🔴 怎么**机器筛**一轮读数（2026-09-22 加；三个字段，别读人读句）

`docs/04` §一 要求「只在静置条件下比较」（`load_snapshot` 必须是 `free`/`free-leftover` 且 `runners=0`），
`AGENTS.md` 要求「跑测量前工作区必须干净」。这两条原来只能**人读**——而人读会误判：
实测过一次"头条记录被自己的护栏字段读成污染轮"（审核 23 §三 A）。现在**三个字段都能机器判**：

| 字段 | 取值 | 说明 |
|---|---|---|
| `load_snapshot` | `status=free runners=0 port=free listeners=0` | **机器可读形式**（`bench.sh verdict`），由 `score_app.sh` 在**拿锁之前**采 |
| `snapshot_note` | 一句话 | 这份快照"什么时候、在什么状态下采的"（防"持锁自采"这类自指） |
| `artifact_fingerprint` | `{git_commit, code_clean, basis, stamped_at_product}` | **产出这份产物的代码干不干净**（首选产物出生处的指纹） |

```bash
# 只挑"静置 + 代码干净"的轮次（示例；字段拿不到就别当通过）
python - <<'PY'
import json, pathlib
for p in sorted(pathlib.Path("runs").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(d, dict) or "app" not in d: continue
    snap = (d.get("load_snapshot") or "")
    fp = d.get("artifact_fingerprint") or {}
    quiet = "status=free" in snap and "runners=0" in snap
    print(f"{p.stem:52s} 静置={quiet} 代码干净={fp.get('code_clean')} 出生处指纹={fp.get('stamped_at_product')}")
PY
```

⚠️ **两种筛法给出的成员不同，引用时必须写明用哪个**（第二十四轮审核 §三 B 点出这条）。

| 筛法 | 判据（**照这个判，别照数量**） | 截至 2026-09-22 19:5x |
|---|---|---|
| **严格法** | 含 `listeners=` **且** `status=free`（= **只认新格式**，`bench.sh verdict` 那种） | **2**：`r4-keep4b` / `r4-keep4-n2` |
| **字段法** | 结论词 ∈ {`free` / `free-leftover`（旧形态文案 `空闲` / `可运行…残留后端`）} **且** `listeners=0` **且**（有 `runners=N` 时）`runners=0` | **14**（含 quickstart 4 + keep 系 10） |
| **字段法 + 代码干净** | 上一行 **且** `artifact_fingerprint.code_clean is True` | 只剩新格式那几份 + 旧记录里补注过 `code_clean` 的（如 `r4-keep4b`） |

- **`load_snapshot` 一共出现过三种形态**（第二十五轮审核 §三 B' 指出我先前漏了第二种）：

  | 形态 | 长相 | 出现在 |
  |---|---|---|
  | ① **全角分号分隔**（老机器可读） | `status=free；runners=0；port=free；ci=<unset>；at=…` | **9/20 那批（`12306-round*`）** |
  | ② **人读句** | `status='🟢 空闲 —— 可以运行 bench 子命令' port3301_listeners=0` | 9/21–9/22 |
  | ③ `=` 分隔（**新格式**） | `status=free runners=0 port=free listeners=0` | 9/22 起（`bench.sh verdict`） |

  ⚠️ ①的 `status=free` 是**真实记录**、不是误记 → 判据若只查 `status=free` 字面量会把 ① 也算进"严格法"（=3）。
  所以严格法必须要求 **`listeners=`**（①没有这个字段）。**收紧后 = 2 ✔**。
  🔴 **但别因此把 ① 排除出"静置轮清单"**——9/20 那两份（`12306-round1` 等）**正是噪声底线（静置序列）的证据**，
  把它们筛掉会让下一个会话找不到噪声底线的来源。**要覆盖全部历史就用字段法。**
- 差别的来源：旧记录的 `load_snapshot` 大多是**人读句**，严格法匹配不到新格式就把它判成"不静置"——
  **那是把"没记成新格式"读成了"没静置"**（同"没记 ≠ 没通过"）。**要看旧的静置轮就必然用字段法**。
- 🔴 **`code_clean` 只在有 `artifact_fingerprint` 的记录里存在**——旧记录**多数没有这个字段**，
  而**未记 ≠ 不干净**：要判旧记录该字段缺省时，回到"跑之前工作区提交干净"那条纪律去查（或直接重跑）。
- ✅ **对本项目当前最重要的比较，两条腿都站得住**：`r4-keep4b`（头条）与 `e1b-keep4`（它的基线）
  **在字段法下都是静置**，且 `e1b-keep4` 的 `pipeline.git_dirty=false`（`r4-keep4b` 的见上表与 `snapshot_note`）。

**为什么要有 `stamped_at_product`**：`pipeline.git_dirty` 原来是在**写记录时**（判分跑完约一小时后）
现采的 → 它 attest 的是那一刻的工作区，**不是产物出生时**。所以现在：
`main.py` 在生成阶段结束时把 `{git_commit, git_dirty, requirement_sha256, app_hint, req_ids, …}`
写进**产物本身**（`<app>/.arc/provenance.json`，实现见 `pipeline/provenance.py`），
`extract_run.py` **优先读它**（`provenance_source=stage-artifact`），读不到才退回现采并标注。

⚠️ **别名提醒**：`load_snapshot` 的**旧记录**里还有两种旧形态——`status='🟢 空闲 —— …'`（人读句）
与 `status='🟢 正在跑…'`（**持锁自采**，只反映自己）。**旧记录照旧读，别按新格式硬筛**；
要筛旧记录就按 `port3301_listeners` / `runners` 这类字段，并按 `docs/04` §一 的限定词标注
（审核 §四.F 的同源提醒：**别比 status 字面量**）。

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

## 2026-09-22 追加：阶梯① `stackoverflow`（第三个 app，**非零**）

| arm | 子集（按 §7 ② 的规则定） | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `so1` | **`{REQ-1.1}`**（helper 最少的候选里，取闭包最小且「未覆盖」栏只含一个**名字**的那个）+ 补充硬名 `questions` | **13,830**（生成段 7,202 + 闭环；预算由新公式推得 564,152） | **1 / 66**（1.0 小时） | **`REQ-1.3.spec.ts:7`（390ms）—— 子集外** | `runs/20260922T143205Z-stackoverflow-score-so1.json` |

**判读（预注册第 ① 格）**：**非零** ✅ → 第 3 个 app 拿到分。
⚠️ **但这一轮的非零不是子集装置换来的**：**子集那条（`REQ-1.1`）自己挂了**，
过的是子集外的 `REQ-1.3`（"偶然通过"）。按闸门口径（只问非不非零）它算数；
按能力口径它**不算证据**。1/66 = 1.5%，其余 65 条属**子集外**（页面/流程不存在）。

**③ 的第一次「漏报」——本轮最有价值的一条**：③ 判分前预测 `REQ-1.1` 会过、实际挂了（FN）。
原因已定位到源码：判据要 `/header/i`、`/sidebar/i`、`/questions/i` 三者**可达**，
而 `namedLocators()` 只在 **8 种 role 的可访问名** + `getByLabel/Placeholder/Text` 里找；
产物里 `header` 只出现在 `<header>` 标签与 axios 的 `headers:` 键里、`sidebar` 只出现在
`aria-label="Sidebar"`（挂在不在那 8 种 role 里的元素上）→ **字符串在源码里 ≠ 文本/可访问名可达**。
→ **③ 的盲区清单两条**：① 流程/页面不存在（bookstack 的两条）；② 字符串可达 ≠ 可访问名可达（本条）。
同一条结论：**别把"预测通过"读成"会过"**，也**别拿它当闸门**（阈值待方案复核）。

**记录质量**：出生处指纹齐（`stamped_at_product=true` / `git_dirty=false`）、
`load_snapshot` 是拿锁前的机器可读形式、**③ 的预测写进了记录的 `predicted` 字段**
（`%TEMP%/so1/predict.txt` 在判分**之前**落盘 —— 它是 9/24 决策规则里 `B_pred` 的唯一来源）。

---

## 2026-09-22 追加：阶梯① `bookstack`（第二个 app，**非零**）

**闸门是"6 个 app 都非零"**（`PLAN.md` §7），顺序按判分成本升序。这是第二个 app。

| arm | 子集（按 §7 ② 的规则定） | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `bookstack1` | **`{REQ-1.1, REQ-2.1}`**（helper 最少的候选里覆盖两个入口面：首页 `openHome` + 登录页 `openLoginPage`）+ 补充硬名 `BookStack` | **18,004**（9 次调用；生成段 10,007 + 闭环） | **2 / 34**（34.7 分钟） | **`REQ-1.1.spec.ts:7`（480ms）** + **`REQ-2.1.spec.ts:7`（468ms）** | `runs/20260922T120254Z-bookstack-score-bookstack1.json` |

**判读（预注册第 ① 格）**：**非零 = 阶梯①的第一段成立**。
⚠️ 2/34 = 5.9% 只说明"这个 app 有分"，**不说明能力**：其余 32 条属**子集外**
（多为"页面/流程不存在"）——闸门只问"非不非零"。

**这一轮把 ②③ 两个工具都用上了，而且都当场兑现**：
- **② 的「未覆盖」栏抓到 `'BookStack'`**（`REQ-1.1` 的 spec 要它显示，而**本子集需求文本没写**——
  全量文本里有，正是 `keep` 的 `Toggle sidebar` 那一类漏法）→ 用
  `PIPELINE_EXTRA_HARD_NAMES=BookStack` 注入。**生成第 1 轮 L1 就报 `accessible_names: 1`**
  （'BookStack' 一次都没出现）→ 修复 → 第 2 轮 0 条。**零成本抓到 + 零成本验证修好了。**
- **③ 的标定（同产物、全量 34 spec）**：**TP=30 FP=0 FN=2 TN=2**（精度 100% / 召回 94%）。
  两条 FN（`REQ-5.3.2` / `REQ-5.5.1`）的失败是**结构性**的（页面/流程不存在），
  而 ③ 只查"名字在不在源码里" → **它的已知盲区：抓得住"名字对不上"，抓不住"流程没实现"**。
  ⚠️ 别把"预测通过"读成"会过"；也**别拿它当闸门**（§7 ③ 的"预测失败 = 0"比闸门更严，待复核）。

**记录质量**：这是**第一份带"产物出生处指纹"**的记录（`stamped_at_product=true`、
`git_dirty=false`、`pipeline_dirty=false`）——指纹在生成结束时盖进产物自己的
`.arc/provenance.json`，不再依赖"写记录时现采"（第二十三/二十四轮审核的那条根因）。

---

## 2026-09-22 追加：预注册复跑 `r4-keep4`（3a+3b 生效）

**它回答的问题**：3a（repair 契约优先级）+ 3b（种子字面量**判死**）能不能把 `REQ-2.1` 拿回来。
预注册（跑之前落盘）：`%TEMP%/r4-keep4/PREREG.md`。基线 = `e1b-keep4`（**1/32，通过的是子集外的 `REQ-2.7.3`**）。

| arm | 配置 | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `r4-keep4`（生成） | 4 条子集 `{REQ-1.1,REQ-2.1,REQ-2.2,REQ-6.2}` + 3a/3b 生效、闭环 ON | **37,290** | — | — | 生成日志 `%TEMP%/r4-keep4/gen.log`（不入库，见上第 3 条） |
| `r4-keep4b`（判分 n=1） | 同一产物，官方档、静置 | —（复用产物） | **2 / 32**（29.0 分钟） | **`REQ-2.1.spec.ts:7`（812ms）** + `REQ-2.7.3.spec.ts:7`（736ms） | `runs/20260922T090406Z-keep-score-r4-keep4b.json` |
| `r4-keep4-n2`（判分 n=2） | **同一份产物**（未重新生成、**零 token**）、预注册 `%TEMP%/r4-keep4/PREREG-n2.md` | — | **2 / 32**（28.9 分钟） | **成员逐条相同**：`REQ-2.1.spec.ts:7`（**726ms**）+ `REQ-2.7.3.spec.ts:7`（448ms） | `runs/20260922T104719Z-keep-score-r4-keep4-n2.json` |
| ~~`…r4-keep4`（第一次判分）~~ | 同一产物 | — | **17/32 中断**（MSYS fork 失败） | 中断产物**不计入任何通过数**（`REQ-2.1` 428ms 只作形态旁证） | `runs/20260922T083438Z-keep-score-r4-keep4.INVALID-interrupted.{log,judges.log}` |

**判读（按预注册第 ① 格）**：`REQ-2.1` **通过** 且 **种子字面量补上** ⇒ **3a+3b 生效**。
⚠️ **不要把结论建在"1 → 2"上**（差 1 条 < 可辨差异下限 2）：它靠 ① **成员翻面**
（`REQ-2.1` 由挂转过，且过在 812ms 的"通过形态"）② **机制链**（3b 第 1 轮硬判报缺 → 修复 → 第 2 轮 0 条 →
产物里字面量真的在，第三只眼 `grep` 复核）。

**n=2（同产物再判一次，零 token）**：**两轮都过、成员逐条相同** → 命中预注册第 ① 格，
`REQ-2.1` 的通过形态在**判分噪声**下复现。三条限定词别丢：
① 只覆盖**判分噪声**（同 `r3a-1/r3a-2`），**不覆盖生成方差**（要覆盖得重跑生成，有 token）；
② **毫秒带有漂移**：9/21 那批通过形态在 **339–478ms**，这两轮 **726–812ms**（仍是 sub-second 通过、
与失败侧 11.1s 不同族，但**别再说"同一带内"**）；③ 通过数差 1 条 < 可辨差异下限 2。

**已知瑕疵（两条，都已在 `docs/12` §十九.3 写明）**：
`r4-keep4b` 的 `load_snapshot` 记成了"正在跑"（**持锁时的自查**，工具 bug，已修；实际启动条件是 🟢 空闲）；
`git_dirty=true` 但**差别只在文档/记录**（`git diff b3c497e --stat -- pipeline/` 为空 → 产物对应的代码干净）。


