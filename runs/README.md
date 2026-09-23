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

## 2026-09-23 追加：阶梯⑥ `12306`（**最后一个 app，非零 → 阶梯 5/6**）

| arm | 子集（按 §7 ② 的规则定） | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `12306-1` | **`{REQ-1.2}`**（helper 最少的那批里，闭包最小 + **未覆盖栏为空** → **无注入**） | **9,663**（生成段 7,675 + 闭环 1 轮全绿、自检 0 条；预算 562,832 = 560,000 + 8×354） | **1 / 135**（2.4 小时） | **`REQ-1.2.spec.ts:7`（522ms）—— 就是子集那条** | `runs/20260923T105954Z-12306-score-12306-1.json` |

**判读（预注册第 ① 格命中）**：**非零** ✅ → 第 5 个 app。
与 stackoverflow（过的是子集外的 `REQ-1.3`）/ prestashop（过的是子集外的 `REQ-2.2.1`）**不同**：
**这一轮的非零就是子集装置自己换来的**（子集那条通过了），也是 **③ 第一次「预测通过」被证实**
（覆盖率 11/11，判分前落盘）。

**机制**：产物是**子集模式的产物** —— 设计只有 **1 条路由 `/`、0 API、0 表**，共 3 个文件
（生成段 7,675 token、6.1 秒）。`REQ-1.2` 的正体就两句（`openHome` + 断言 `Quick Guide` 可见），
产物首页渲染了它；其余 134 条要的注册 / 登录 / 车次查询 / 下单等流程**这个子集根本没要** →
**全部 timeout**（`failure_class_counts={'timeout': 134}`、`ERR_CONNECTION_REFUSED=0`，
即**没有**基础设施失败）。两处形状已核：① `App.tsx` **内联**了一份自己的 `HomePage`
（`pages/HomePage.tsx` 从未被 import）——判据拍的可访问性树证实**渲染的是内联那份**；
② 首页文案是通用填充（"Welcome to our store…"），与 12306 无关，但判据只查 `Quick Guide`。

**口径**：零人工（产物一个字节没手改）；**静置**（拿锁**之前**采的快照
`status=free runners=0 port=free listeners=0`）；`code_clean=true` / `pipeline_dirty=false`
（出生处指纹 `stamped_at_product=true`）；**无 n=2** —— 这一分在**判分噪声**下稳不稳**未测**
（keep / stackoverflow / prestashop 都测过，12306 没测）；通过数 1 条低于可辨差异下限 2，
**不与其它轮次比多寡**，它只回答"这个 app 有没有分"。

---

## 2026-09-23 追加：bookstack **深度第一刀**（首阻清了，但成员没动 —— **预注册第 ④ 格**）

| arm | 子集 | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `bookstack-deep1` | **`{REQ-1.2, REQ-2.2, REQ-4.1, REQ-5.1}`**、**零注入**（② 未覆盖栏为空） | **89,568**（生成段 45,321 + 闭环 44,247） | **2 / 34** | `REQ-1.1`（431ms）+ `REQ-2.1`（465ms）—— **与 `bookstack1` 逐条相同** | `runs/20260923T145411Z-bookstack-score-bookstack-deep1.json` |

**判读（预注册第 ④ 格命中）**：**「首阻清了、而成员没中」**。
- ② 复核确认修 `classify_helper` 之后 `Books`/`Shelves`/`Email address` 不再躺在「测试自己输入」那一栏
  （`REQ-5.1`：0 需要显示 / 2 输入 → **2 / 0**）；
- 但**成员一条没动**：`REQ-4.1`、`REQ-5.1` 都没过，通过数与成员**与 `bookstack1` 逐条相同**。

**新首阻 = 种子数据（两个工具独立同向指认）**：L1 结尾 **5 条 `seed_literals` 缺失**，
而 ③ 对 `REQ-4.1` / `REQ-5.1` 报的缺口正是 `[fixture:need] 'Shelf 4.1'` 与 `'Book 5.1'`；
**回产物核过：这些字面量在前端与后端都不存在**（**不是假阳性**）。
→ 下一步按预注册写死的方向：**补种子数据**，不是再改机制。

**③ 的第二个标定点（bookstack 第 2 份产物）**：覆盖率 **451/451**、**TP=32 / FP=0 / FN=0**、精度/召回 **100%**
（第一份产物也是 100%/100%）→ ③ 在两个独立产物上都成立。
⚠️ **③ 是判分之后才跑的**（PREREG 写的是「判分前落盘」，**这一条没照做**）→ **不是预注册命中**，记录里如实标注。

**口径**：**条数 2 < 4**（保留门槛）→ **本轮不记账**；零人工 / 静置 / 出生处指纹 `f66b8492` + `pipeline_dirty=false` / n=1。

---

## 2026-09-23 追加：阶梯⑤ `ctrip` 第二轮（**非零 → 阶梯 6/6 达成**）

| arm | 子集 + 注入 | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `ctrip2` | **`{REQ-2.2}`** + **注入 `[4]` 的全部 7 行**（PLAN:1268「不挑不拣」）+ 生成前提交的**落点约束**（`a96163d`） | **33,069**（生成段 15,788 + 闭环 17,281） | **2 / 125**（1.5 小时） | **`REQ-2.2.spec.ts:7`（752ms）—— 就是子集那条** + `REQ-2.3.1`（675ms） | `runs/20260923T132344Z-ctrip-score-ctrip2.json` |
| ~~`ctrip1b`（第一轮）~~ | 只注入 7 条里的 1 条（`账号登录`）、**没有落点约束** | 26,302 | **0 / 125** | 无 | `runs/20260923T094815Z-ctrip-score-ctrip1b.json` |

**判读**：**非零** ✅ → **六个 app 全部非零（6/6）**，`PLAN.md` §7 的阶梯闸门达成。
**PLAN 的验收条件 1 在真判分上兑现**：`REQ-2.2` 从"预测失败"转"预测通过"（③ 判分前落盘），
**并且真的通过了** —— 这是"**硬清单回灌 + 落点约束**"这条链的**第一次端到端验证**。
⚠️ **一轮不变量的核对**：这一轮**只变一处**——注入 1 条 → 7 条（+ 落点约束那一句）；
其余逐字不变。通过数 **0 → 2**（Δ=2 = `docs/04` 的**可辨差异下限**；且成员里**含子集那条**，
不是像 stackoverflow / prestashop 那样只有子集外的偶发）。

**机制（回产物核过）**：7 条注入**逐条落在各自单个元素**里 ——
`<h1>账号登录</h1>`、`<p>邮箱/用户名/手机号</p>`、`<p>email/username/mobile</p>`、`<p>account</p>`、
`<label>密码</label>`、`<p>password login</p>`；首页 `<Link to="/login">登录</Link>` 供
`openLoginPage` 的 `clickFirstAvailable` 命中。`expectPasswordLoginForm` 的三个单元因此都能被
`getByText` / `getByRole(name)` 打到。

🔴 **`final_l1=False` 的正确读法**（记录 `notes` 里也写了）：原因**是注入形态，不是"产物没过 L1"** ——
`[4]` 的行是**正则链的源码**（`邮箱.*用户名.*手机号`），当"逐字硬名"注入后
`check_accessible_names` 的字面子串检查**永远追不到**（产物里是 `/` 连接的自然写法，而判据的正则恰恰要那个）。
代价：闭环 **17,281 token（全程 33,069 的 52%）**、三轮 `accessible_names` findings **5 → 2 → 2**。
→ 这条已由**裁决四**（L1 对正则体改用可命中性、与 ③ 共用实现）治掉；
**验收已过**：同一份产物 + 那 7 条名字 → L1 findings **0**。

**口径**：零人工 / 静置（拿锁前快照 `free-leftover` + `runners=0` —— `free-leftover` 是上一轮留在 3301 的后端，
按 `AGENTS.md` 属"无害"、按 `docs/04` §一 仍算静置）/ 出生处指纹 `a96163d` + `pipeline_dirty=false` /
**无 n=2**；Δ=2 条刚好到可辨差异下限，**不据此宣称"机制普遍有效"**（N=1 个 app、1 个产物）。

---

## 2026-09-23 追加：阶梯④ `ctrip` 第一轮（第五个 app，**当时尚未非零**）

| arm | 子集（按 §7 ② 的规则定） | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `ctrip1b` | **`{REQ-2.2}`**（helper 最少 + 闭包最小 + 未覆盖栏最小）+ 注入 `账号登录` | **26,302**（5 块；第 1 轮 L1 抓到注入名 → 修复 → 第 2 轮 0 条） | **0 / 125**（1.4 小时，全部 timeout） | **无** | `runs/20260923T094815Z-ctrip-score-ctrip1b.json` |
| ~~`ctrip1`（第一次判分）~~ | 同上 | — | **71/125 中断**（MSYS fork 失败） | 中断产物**不计入**（子集那条 11.9s 挂，只作机制旁证） | `runs/20260923T090935Z-ctrip-score-ctrip1.*.INVALID-interrupted.*` |

**判读（预注册第 ② 格命中）**：**0 通过**，而 **③ 判分前就预测了子集那条会挂** →
所以这一轮不是「跑了但没分」，是**子集装置本身没兑现**。
⇒ 阶梯的「6 个都非零」停在 **4/6**（keep / bookstack / stackoverflow / prestashop），**ctrip 与 12306 待拿**。

**机制（回产物 + 判据日志核过）**：产物**渲染了** 登录 / **账号登录** / 用户名 / 密码，
但判据要的是**能被 locator 命中的那个 pattern** —— 失败行是
`getByRole('button', { name: /邮箱.*用户名.*手机号/ }).first()` 与 `.../注册/...`：
`expectPasswordLoginForm` 的**每个要求单元**都要有一个**可命中的元素**，而单元的候选是**复合串**
（`邮箱.*用户名.*手机号` / `email.*username.*mobile` / `account`），产物只有「用户名」这种**片段**。
→ **不是「没做」，是「没按判据要求的串写」**（与 prestashop 的「译了」同属契约类，这条更进一步：**复合 pattern**）。

**下一轮的靶子已经明确（零 token）**：③ 的 unreachable 列表就是硬清单的输入 ——
把**单元**（而非单词）交给 `PIPELINE_EXTRA_HARD_NAMES`，并在 prompt 里说明
「单元 = 一个元素的可访问名要匹配这个 pattern」。⚠️ 这属**方案会话在拍**的
「③ 的 unreachable 回灌硬清单」，本轮不自己拍。

### 📋 待注入清单（ctrip 复跑用；**判分窗口里零 token 整理，未执行**）

证据三处：① 记录 `predicted.missing_units`（判分**前**落盘 `%TEMP%/ctrip1/predict.txt`）；
② 判分失败行 `getByRole('button', { name: /邮箱.*用户名.*手机号/ }).first()`（`runs/*ctrip-score-ctrip1b.judges.log:358`）；
③ **判据自己拍的可访问性树快照** `repos/arc-bench/test-results/ctrip/ctrip-tests-REQ-2.2-*/error-context.md`
（失败那一刻的页面，不是我们的推断）。

失败那一刻页面上**确实有**的（③ 的原文，未改一字）：

```yaml
- heading "账号登录" [level=1]
- form "账号登录":
  - text: 用户名   /   textbox "用户名"
  - text: 密码     /   textbox "密码"
  - button "登录"
```

→ `/密码/`（text 密码）与 `/登录/`（heading + button）的元素**都在快照里**；
**唯一没有对应元素的是单元 1**。

| # | 要注入的串 | **它必须落在哪**（判据的落点约束） | 判据路径 |
|---|---|---|---|
| **1** | **`邮箱/用户名/手机号`** | **单个元素的可见文本**，三个词**按此序**出现在**同一个元素**里。可命中的类别 = `button / link / tab / menuitem / option / radio / checkbox / heading` 的**可访问名**，或**任意元素的文本节点**（`getByText`）。→ **最稳的落点**：登录表单账号输入的 `<label htmlFor="username">` 文本写成这一串（同时满足 `getByText` 与 `getByLabel`，别的 spec 的 `fillField` 也吃得到） | `expectPasswordLoginForm` → `expectAnyVisible` → `expectVisible` → `resolveNamed` → `namedLocators`（`helpers.ts:321-334`） |
| 2 | `密码` | 同上（文本节点即可）—— **失败快照里已有** | 同上（单元 2） |
| 3 | `登录` | 同上，且要**可点击可见**（`openLoginPage` 先 `clickIfVisible`，失败会走 `clickNamed` 硬点）—— **失败快照里已有** | `openLoginPage`（`helpers.ts:585-588`） |
| 4 | `账号登录` | **可选**（`clickIfVisible` 失败不抛错）；上一轮已注入过 | `ensurePasswordLogin`（`helpers.ts:598-602`） |

⚠️ **三条语义必须带进 prompt，否则注入了仍可能不命中**：
1. **`.*` 不跨元素** —— `邮箱` / `用户名` / `手机号` 分散在**三个**元素里**不算**，必须是**同一个元素**的文本；
2. `resolveNamed` 的**兜底只试第一个候选**（`patterns[0]`，`helpers.ts:358`）→ 要落的是**第一个候选**
   `邮箱.*用户名.*手机号` 的**字面形态**（`邮箱/用户名/手机号`），别只落 `/account/i` 那种备选；
3. 判据**只认那 9 类位置**（8 种 role 名 + 文本节点）—— 把串塞进 `placeholder` / `name` 属性**对它不算命中**
   （那是 `fieldLocators` 那一族，`expectVisible` 不走它）。

> ⚠️ **证据只到"元素在快照里"这一层**：`expectAnyVisible` 是**按顺序**逐个 `await` 的，
> **单元 1 一抛就中止** → 单元 2/3 **没有被执行到**；"它们没问题"是**推断**，不是实测。
> 复跑的三格预注册见 `%TEMP%/ctrip1/PREREG-rerun.md`（本轮备好，**等方案拍板**才跑）。

### ⚠️ 回灌的**真实到达面**（判分窗口里零 token 核过，供方案拍板用）

全仓 grep `EXTRA_HARD`：**只有一处读取** —— `pipeline/main.py:504`，而它在**验证闭环**那一段里。
所以注入的硬名目前只到这两处：

| 到达 | 证据 |
|---|---|
| ✅ **验证闭环的 brief**（模型自检 + 定向修复的 prompt） | `main.py:508` 用 `extra_hard` 构 brief → `verify_loop(requirement_brief=brief)` → `verify/loop.py:74`（`review()`） |
| ✅ **L1 的 `required` 契约**（`check_accessible_names`，**驱动修复**） | `main.py:532`（`required = […pick 的静默名…] + extra_hard`） |
| ❌ **初始生成**（`design` 那一次 + 各块实现） | `implement.py:459` 的 brief **没带 `extra_hard`**；`main.py:478` 调 `generate_app` 时**也没传**它 |

→ **含义**：注入的名**不是"生成时就知道"**，而是**靠 L1 抓到 → 修复轮补上**
（ctrip 第 1 轮 L1 抓到 `账号登录`、第 2 轮 0 条，走的正是这条路径）。
→ **对复跑的意义**：cell ③（"串仍不在产物里"）**要读成"回灌没进 prompt"之前，先分清是哪一段 prompt** ——
初始 prompt 里**本来就不会有它**；有它的是**修复轮**。
→ **这也是方案那条决定的一部分**：若要让复合串**一开始就按「同一个元素、按序」落**，
得把它接进 `generate_app`（一行 plumbing），并**在两条 prompt 上都写落点约束**。

### 🔎 备选路径扫过（零 token）：**没有"换个 ctrip 子集绕过去"的便宜路**

把 ctrip 的"入门级"候选逐个跑了 ②（`subset_closure.py ctrip <REQ>`，不改任何东西）：

| 候选 | 闭包（具体） | 「未覆盖」栏 | 判据形状 |
|---|---|---|---|
| **`REQ-2.2`**（本轮用的） | 22（10） | **2**（含复合串） | 1 个复合串 + 2 个已有的 |
| `REQ-2.3.1` | 22（13） | 1（`账号登录`） | ⚠️ **与 REQ-2.2 是同一个调用**（spec 正文都只有 `await h.ensurePasswordLogin(page)`）→ **同样需要那个复合串** |
| `REQ-2.3.2` | 21（12） | 6 | — |
| `REQ-0` / `REQ-1.1` | 17（8） | **8**（全是**单词**：`login` / `register` / `search` / `sign in` / `sign up` / `搜索` / `注册` / `登录`） | 无复合串，但 **8 个必须全可见**（缺一个就挂） |
| `REQ-7.1` / `REQ-9.1` | 18（9） | 3（`more services` / `更多服务` / `机票`） | 要先点开「更多服务」 |

→ **结论**：换 REQ 换不掉缺口（2.2 与 2.3.1 同源）；没有复合串的那两个要 **8 个名字全中**。
**方案那条决定确实是关键路径** —— 但这不影响本轮：本轮只做 12306。

### 💡 顺带（供方案把决定的范围缩小）：现有机制**可能已经够**

`check_accessible_names`（`l1.py:174-189`）的判据是**前端源码里的子串**；而修复 prompt 里那句
「同样按**可访问名或可见文本**兑现，不得改名」——把 `邮箱/用户名/手机号` 写成**一个** `<label>` 的文本，
**同时**满足 L1 与 locator（`getByText` 命中该 label）。
→ 需要补的可能只是「**同一个元素、按序**」这一句约束，**不是**一整套新机制。

---

## 2026-09-23 追加：阶梯④ `prestashop`（第四个 app，**非零**）

| arm | 子集（按 §7 ② 的规则定） | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `ps1` | **`{REQ-2.1}`**（helper 最少的候选里，取「未覆盖」栏**为空**的那个）+ **无注入** | **22,356**（7 块；预算 562,704 由新公式推得；**第 1 轮 L1 就全绿**） | **1 / 86**（1.5 小时） | **`REQ-2.2.1.spec.ts:7`（5.1s）—— 子集外** | `runs/20260923T022226Z-prestashop-score-ps1.json` |
| `ps1-n2` | **同一份产物**再判一次（零 token；预注册 `%TEMP%/ps1/PREREG-n2.md`） | — | **1 / 86**（1.4 小时） | **`REQ-2.2.1.spec.ts:7`（5.0s）—— 同一条**；**失败集合也逐条相同** | `runs/20260923T074105Z-prestashop-score-ps1-n2.json` |

**判读**：**非零** ✅（第 4 个 app）。

**n=2（同产物再判一次）**：**同一成员、失败集合逐条相同** → 与 stackoverflow 同型，这一分在**判分噪声**下站得住（限定词同前：不含生成方差；子集外偶发通过，用途只是「闸门只要非零」）。⚠️ 与 stackoverflow 同形：**子集那条（`REQ-2.1`）挂了**、
过的是**子集外**的偶发通过；1/86 只说明「这个 app 有分」。

**③ 在判分前就预测对了**：`REQ-2.1` 预测**失败**（4 个靶子里 3 个不可达：`popular products` /
`newsletter` / `footer`），实际确实挂 → 这是 ③ 收紧（loose 靶子必须落在 locator 可命中的位置）之后的
**第一次"正确预测失败"**。回产物核过的原因是**新失败形态**：需求文本是英文术语，模型**把它们译成了中文**
（产物里是 热门商品 / 促销活动 / 订阅我们的资讯）→ 判据找不到英文原词。
**不是"没做"，是"译了"**；`PIPELINE_EXTRA_HARD_NAMES` 那套机制正好能治它（注入 → brief 的
「必须逐字兑现」块 + L1 契约），但本轮按预注册**照原样判分**（③ 只当信息），
「把 ③ 的 unreachable 列表回灌硬清单」列为**待拍板**的后续。

**记录**：`predicted` 字段已写入（9/24 的 `B_pred` 来源）；出生态指纹齐；
顺带修了 extractor 两处（记录的 `pipeline` 块漏了 `pipeline_dirty`、`code_clean` 原来只看仓库级
`git_dirty` → 现在优先按 `pipeline/` 判定，所以本记录是 `code_clean=true` 而 `git_dirty=true`，
两者**都写明**依据）。

---

## 2026-09-22 追加：阶梯① `stackoverflow`（第三个 app，**非零**）

| arm | 子集（按 §7 ② 的规则定） | 生成 token | 判分 | **通过的是哪条** | record |
|---|---|---|---|---|---|
| `so1` | **`{REQ-1.1}`**（helper 最少的候选里，取闭包最小且「未覆盖」栏只含一个**名字**的那个）+ 补充硬名 `questions` | **13,830**（生成段 7,202 + 闭环；预算由新公式推得 564,152） | **1 / 66**（1.0 小时） | **`REQ-1.3.spec.ts:7`（390ms）—— 子集外** | `runs/20260922T143205Z-stackoverflow-score-so1.json` |
| `so1-n2` | **同一份产物**再判一次（零 token；预注册 `%TEMP%/so1/PREREG-n2.md`） | — | **1 / 66**（1.1 小时） | **`REQ-1.3.spec.ts:7`（768ms）—— 同一条**；**失败集合也逐条相同** | `runs/20260923T011156Z-stackoverflow-score-so1-n2.json` |

**判读（预注册第 ① 格）**：**非零** ✅ → 第 3 个 app 拿到分。

**n=2（同产物再判一次）**：**同一成员、失败集合逐条相同** → 这一分在**判分噪声**下站得住（限定词：只覆盖判分噪声、不含生成方差；且它是子集外的偶发通过，用途只是「闸门只要非零」）。
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


