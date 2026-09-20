# 进度板

> **每个会话开始先读这里，结束前更新这里。**
> 格式约定：只记"状态"，不记推理过程。推理和证据放 `PLAN.md` / `docs/` / `runs/`。

**最后更新**：2026-09-20

---

## ⚠️ 在飞工作（并发会话必读）

**这个工作区会被多个会话同时操作。开工前先看这一栏。**

| 会话/时段 | 正在做什么 | 开始时间 | 状态 |
|---|---|---|---|
| — | 无 | — | — |

**已结束（供并发检查对账）**：
- 9/20 **12:0x**（不是晚上）**方案层**会话 —— 只读测量 + 方案层落盘，
  改动 `docs/06-需求契约实测.md`、`PLAN.md`（§4.1 / §8 R5 / §11）、`STATUS.md`、`runs/README.md`，
  新增 `docs/06-a11y-recall-probe.py`、`runs/20260920T-a11y-recall-all-apps.txt`。
- 9/20 **12:1x** 继续同一会话：收口第五轮审核的批评 ——
  补 `runs/20260920T-interaction-unit-a11y.txt`、更新 `docs/06` §三补-2 / `PLAN.md` §4.1 / `STATUS.md`；
  R5 全套验证（含 `arc doctor` exit 0）。**仍未碰 `pipeline/`、`eval/`、`repos/`。**
**未碰 `pipeline/`、`eval/`、`repos/`**；未跑任何 bench 子命令（开工前 `bench.sh status` 已确认锁/端口/runner 全空）。

### 认领与清场规则（硬规则）

1. **开工前先看这一栏。** 非空且时间近 → 要么等，要么认领一块**不重叠**的区域。
2. **认领要写进来**，结束会话时清空。没写就等于没认领，别人会重复做或覆盖。
3. **动测试之前先 `./eval/bench.sh status`。** 有锁或端口被占 → 不要跑任何 bench 子命令（包括 `reset`、`stop`）。详见 `AGENTS.md` 的多会话并发一节。
4. **会话结束前必须清场**：停掉自己起的后台进程；未完成的工作连同**日志路径**和**进行到第几条**一起写进这一栏，不留孤儿。

---

## 当前阶段

**M3b-0 自动化闭环已完成 → 两条判据全部达成**（零人工 6/6；held-out 零人工构建成功）。
第 1/3 阶段 + 生成 + **L1 闸门 + 模型自检 + 定向修复**都已跑通；
**下一步是 M3b-1：把「跑通」铺到规模（6 任务全部非零）**。

下一步是把最小闭环从"能解析"推进到"能生成"：quickstart（单 REQ-1）→ 生成 → 起服务 → 跑官方测试。

---

## 已完成

### 信息与决策

- [x] 官网信息抓取与复核（`README.md` + `raw/`）
- [x] 研习营三天转录稿转文本并精读（`class/`）
- [x] 8 个角度的并行调研，结论落盘 `docs/01`–`05`
- [x] **路线决策：自研薄管线**（`PLAN.md` §2）
- [x] 方案定稿并修订：`PLAN.md`（新增 §4.3 墙钟预算与降级；§4.2 定死"只生成场景探针"）
- [x] 跨会话结构：`AGENTS.md`（含多会话并发协议）+ `STATUS.md` + `docs/`

### 环境与验证（V1–V4）

- [x] **V1 测试契约审计** — `npm run test:audit` 通过
- [x] **V2 上界基线** — 12306 参考实现本地跑通（绕过 Docker）。注意：**只有 12306 带参考实现**
- [x] **V3 噪声底线** — DB 状态累积是主要噪声源。**两个口径**：失败条数波动 **0–6（跨度 6，五轮）**；会翻面的测试共 **11 条**；**零稳定失败** → 实测区间 **129–135**，R5 满分（135）。⚠️ 跨度由 4 变 6，门槛的算术前提已变，待裁定
- [x] **V4 网关连通** — key 有效，16 个模型可列，计费含 reasoning token

### 9/20 12:0x 新增（方案层）

- [x] **R5 依赖兼容性 —— 安装阶段已过；ARC 本体尚未真实跑过**（原风险项，`PLAN.md` §8）
      **已验证**（Python 3.13.11，临时 venv，未污染工作区）：
      1. `requirements.txt` 全部 **13 个依赖安装成功**
      2. 两个重依赖 **`deepagents` + `langchain_openai` import 成功**（首日 `ModuleNotFoundError` 的主角）
      3. **ARC 入口 `repos/agentic-requirement-compiler/src/main.py` import 成功**（完整 import 图）
      4. `arc --help` 正常；**`arc doctor` → `✓ Configuration is valid`（exit 0）**
         （需显式加载工作区 `.env`：ARC 只找**当前目录**的 `.env`）
      → **结论：环境齐备，不需要回退 3.11/3.12。** ⚠️ **但 `arc compile` 从未跑过一次**——
      那需要真实 LLM 调用；9/23 的保底包就是它，**这一步仍待做**（见「下一步」第 6 条）。
      复现（临时目录，不污染工作区，`-B` 避免写 `repos/` 的 `__pycache__`）：
      ```bash
      python -m venv "$TEMP/r5probe" && "$TEMP/r5probe/Scripts/python.exe" -m pip install -r pipeline/requirements.txt
      "$TEMP/r5probe/Scripts/python.exe" -B -c "import deepagents, langchain_openai; print('heavy imports OK')"
      cd repos/agentic-requirement-compiler/src && "$TEMP/r5probe/Scripts/python.exe" -B main.py doctor
      ```
- [x] **六 app 可访问名召回抽检**（`PLAN.md §4.1` 指定"必须在批量生成之前做"的那件事）
      探针 `docs/06-a11y-recall-probe.py`（审计快照），输出存档
      `runs/20260920T-interaction-unit-a11y.txt`（交互口径）+
      `runs/20260920T-a11y-recall-all-apps.txt`（收窄前版本，保留供对照）。
      **关键数字——L1 闸门空转率（交互单元 / 全部单元两个口径）：**
      `12306 2%/5% · ctrip 18%/26% · bookstack 19%/24% · stackoverflow 29%/44% · keep 40%/50% · prestashop 50%/62%`
      **诊断：差异是种类不是程度**——12306 是引号式需求（`labeled "Name"`），
      其余四个是**散文式**（`Display Logo, category menu, search box`，零引号）+ 测试用 `/logo/i` 松散正则；
      抽取器入口 `_iter_quoted()`（`a11y.py:219`）**按构造抽不到无引号名词**。
      → 详见 `docs/06` §三补 / §三补-2 与 `PLAN.md` §4.1（含裁定与三个约束）

### 9/20 12:2x–12:4x 新增（执行层：`eval/bench.sh` 并发安全 + 第 5 轮基线）

- [x] **`eval/bench.sh` 防事故 5 条全部落地并验证**（见下方「第二轮审核遗留 · ✅ 并发安全」）
      其中第 2、3 条判据是**审核连续三轮点名"唯一会毁数据的项"**——现在修完了。
      **验证的关键一条**：在**真实测量进行时**（R5）抢锁被拒 + `status` 给出
      `🟢 正在跑，不要动`（证据含端口占用、3 个 runner 活着、锁持有者活着）——
      那正是两次 run 作废的事故场景。
- [x] **第 5 轮干净基线：135/135（3.0 分钟）** —— `runs/20260920T043334Z-12306-round1.log`
      / `.json`（`arm_id=refimpl-baseline-r5`，`pass_rate=1.0`）。
      🔴 **它推翻了两条已写进 `AGENTS.md` 的结论**（`REQ-4.3.7` 稳定失败、理论天花板 134/135），
      全部文档已按事实改正（`AGENTS.md` / `PLAN.md` §6+§11 / `docs/04` §三+§四 / `runs/README.md` / 本文件）。
      ⚠️ 它同时把噪声跨度从 4 抬到 6 —— **门槛的算术前提变了，那属"测量方法"，已挂起待裁定**。

### 9/20 13:2x–13:5x 新增（第六轮审核的 4 条整改）

- [x] **2.1 修掉一个我引入的死锁缺陷 → 新增 `./eval/bench.sh recover [--force]`**
      （详见「下一步」第 1 项的说明）。**已用造出来的死锁现场端到端验证**：
      死 PID 锁 + 端口被占 → `test` 被拒（复现审核描述的"常规子命令全部不可用"）→
      `recover` 预检通过 → `--force` 恢复 → `status` 回 🟢；
      **安全属性也测了**：活锁 → 拒；死 PID 但心跳新鲜 → 拒（防"把正在跑的测量强杀掉"）。
- [x] **2.2 记录缺字段 → RunRecord 新增 3 个字段**
      `ci_env` / `retries_effective`（`playwright.config.ts` 的 `retries: CI ? 1 : 0` → 口径可判别）/
      `retries_observed`（从日志里数）/ **`load_snapshot`**（本轮**开跑前**的环境快照：
      `status=…；runners=…；port=…；ci=…`，取锁**之前**采集，记的是外部世界的状态）。
      已实测：无 CI → `retries_effective=0`；`CI=1` → `1`。
- [x] **2.3 `R5-收口` ①② 完成**：工作区根建了**正式 `.venv`**（Python 3.13.11）→
      `import main` 通过 + **`arc doctor` → `✓ Configuration is valid`（exit 0）**；
      `pip freeze` 存档为 **`docs/07-依赖已知可用版本集.txt`**（104 行，含采集时验过的三条与关键版本）。
      其中 `deepagents==0.7.15` 是**私有路径风险的落点**（ARC 在 `factory.py:10-11` 模块级导入
      `deepagents._models` / `deepagents.backends.filesystem._raise_if_symlink_loop`）。
      ⚠️ **③ 仍未做：`arc compile` 从未真正跑过**（要真实 LLM 调用）——
      在它跑通之前，"保底包能上榜"仍只是假设。
- [x] **2.4 `STATUS.md` 的内部矛盾已消除**（同一件事既写"已落地"又写"仍未落地"）。
- [x] **补充项**：`bench.sh` 现在同时往 **`runs/.inflight.log`** 追加认领/释放记录
      （只追加、从不修改 → 无覆盖风险）；`AGENTS.md` 的例外说明已更新为"已补"。
- [x] `.gitignore` 补 `.venv/`。

### 9/20 14:4x 新增（V5 前置：提交包 + 平台侧事实）

- [x] **提交包构建 + 合规校验工具化**：新增 `eval/package.sh`
      （在 `pipeline/` 内部打 zip，保证第一层就是 `main.py`；然后逐条校验 `docs/02` §一 的硬规则）。
      `output/bundle.zip`（136K）**全绿**：第一层有 `main.py`、根层无 `package.json`/`index.js`/`index.ts`、
      无 `.env`、无 `__pycache__`/`.git`/`node_modules`。
      → 9/28–29 彩排要反复打包，这个脚本就是那次彩排的尺子。
- [x] **V5 的入口已摸清，并核对到三条会影响方案的平台事实**（打开页面实测，非推断）：
      ① **平台要我们自己提供 API Key**（表单字段必填）→ 计量挂在我们交的 key 上；
      ② **Model / Visual Model 在提交时指定**，名字与 `.env` 的 `MODEL`/`VISUAL_MODEL` 同名；
      ③ 提交 = 一个快照 + **之后逐任务选跑**（只跑 1 个任务不产生聚合分、不污染榜单）。
      另：**平台侧逐任务模块/测试数已逐项对上**（468 / 484，本地 478，差 +6）。
      → 详见 `docs/08-V5-提交操作指导.md`
- [x] **顺带确认**：`ACCOUNT.md` 里的"待确认"三项早已确认，平台地址与用途表仍然准确。

### 9/20 15:3x 新增（V5 第一轮结果 → 修掉"平台第一道闸"）

- [x] **V5 第一次提交已发生（用户手动上传 + 跑六个任务）** —— 平台报错：
      `Your run failed: web template is incomplete: expected frontend/ and backend/ directories`
- [x] **诊断：这条闸检查的是"run 产出了什么"**，不是"zip 里有什么"。证据：
      ① 报错是 per-run 的 "Your run failed"；② ARC 自己第一步就 `copy_template()`
      把 `web-react-express/`（含 `frontend/`+`backend/`）拷进 workspace；
      ③ 我们的 `cmd_compile` 只写事件 + traceability，**从不拷模板**。
- [x] **修复（第 3 阶段最小版）：新增 `pipeline/generate/scaffold.py`**，
      把模板内容拷进输出目录，产出 `frontend/` + `backend/`；模板查找优先级
      `ARC_AGENT_TEMPLATES_ROOT` → `ARCBENCH_TEMPLATE_DIR` → 包内 `templates/`；
      **已有应用不覆盖**（兼顾决赛 evolution）；找不到模板抛错并写 run-failed 事件。
      **本地已验证**：quickstart 编译后输出根 = `backend/ frontend/ reqcompile/ README.md template.yaml`。
- [x] 重新打包 `output/bundle.zip`（141K，合规校验全绿），提交名建议 `probe-v2-scaffold`。
- 🔴 **仍未拿到的 V5 信息**：那一跑里 `doctor` 打出的 `ARCBENCH_*` / argv / cwd
      （平台怎么调我们用）——见 `docs/08` 第二轮的"请回传"清单。

### 9/20 16:0x 新增（**V5 完成：平台怎么调我们，全部已成事实**）

- [x] **V5 结论（实测日志 `runs/2d5a2a44160f`，Stdout 面板 2881 行）** —— **权威记录 `docs/09-平台提交与报错记录.md`**。要点：
      · **调用**：`python3 /workspace/submission/main.py <需求目录> --output-dir /workspace/template`
        （需求目录是**位置参数**，输出用 **`--output-dir`** 长选项；两者我们都支持 ✓）
      · **输出目录 = `/workspace/template`**，`ARCBENCH_*` 都指向它下面的 `.arc/`
      · **平台注入 `MODEL` / `VISUAL_MODEL`** → 之前"平台把它们当环境变量注入"的推断成立
      · **平台自带模板** `/opt/arcbench/templates/web-react-express`，我们的查找优先级正确命中它 ✓
      · **平台自己装依赖 + 构建 + 起服务**（frontend 290 包 / 16s → build → backend → 3000 可达）
        → **`frontend/dist/index.html` 由平台生成，我们不必在 agent 里构建**（此前的未决问题解除）
      · **平台读到了我们的 traceability**：`Traceability initialized with 109 requirements and 86 scenarios`
        （= PrestaShop 精确值）→ 第 1 阶段产物被消费 ✓
      · **Python 3.12**（本地 3.13，我们的代码无 3.13 专属特性）
- [x] **第二次提交的结局**：`generation-agent exit code: 0` → 平台构建/起服务全部成功 →
      测试 `passed=0, failed=86, score=0.0` → `Runner exited with test failures or runtime errors`。
      **原因纯粹是"应用只有骨架、没有功能"**，不是基础设施故障。
- 🔴 **我们不在榜上**（`/competition?competition=arc-bench-web` 实测 15 行，无 `suisui`）。
      榜单分 **Senior（≥80%）/ Junior（<80%）** 两级；Junior 只展示排名+用户+通过率+覆盖度。
- ⚠️ **待解现象（不要据此下结论）**：前 6 名里多名队伍 **token ≈ 0 / runtime ≈ 0 却拿到 80–100% 通过率**。
      要么他们的应用不是现场用 LLM 生成的（会撞"不得预埋答案"红线），要么计量口径有我们还没理解的机制。

### 9/20 18:0x–18:5x —— ✅ **M3a 能力探针完成：机制成立**（`docs/10-M3a能力探针.md`）

- [x] **`pipeline/` 第一次真的调用 LLM 生成功能**。新增 `generate/llm.py`（自写 HTTP + 逐次 usage 落盘 + 重试）、
      `generate/implement.py`（1 次设计 + **4 个分块**实现）、`main.py` 接线
      （`PIPELINE_GENERATE` / `PIPELINE_REQ_IDS` 开关）。
- [x] **判据：Lab04 的 6 条验证测试**（quickstart 的 REQ-1，1 个需求 / 3 场景 / 10 个可访问名）。
      **结果：3/6（原始产物）→ 3/6（修模板 bug 后）→ ✅ 6/6（手工最小修复前端 3 处后）**
      → **按预登记的分叉表命中第一行"机制成立"，且修复后满分**。
- [x] **修掉一个上游模板 bug（我们只是受害者）**：`init_db.js` 的 `initializeDatabase()`
      第二次调用起返回 `initPromise`（resolve 成 undefined）→ `db_runtime` 的
      `run/get/all/exec/withTransaction` 全崩。新增 `scaffold.py::apply_known_fixes()`，
      **修在产物上**（因为脚手架优先用平台自带模板，只改自己副本修不到平台那份）。
- [x] **三个失败根因查清**：① 一处 import 路径写错（构建失败）；② 上游模板 bug（见上）；
      ③ 前端三处接线/瞄准缺陷（`onRegistered` 没传、注册没走 auth context、用户名没单独成元素）。
- [x] **成本首次有数字**：**89,948 token**（in 21,417 / out 68,531），
      **其中 reasoning 59,972 = 输出 token 的 87%** ⚠️ —— 推理是主要成本，且同样计费。
- [x] **接上 DeepSeek 官方测试档**：`.env` 里 `PIPELINE_LLM_*` 三行（**.env 不提交**），
      本地迭代不再烧比赛额度。对照：同一句"只回两个字"，网关 97 token / 官方 10 token。
- [x] **仪器校验已做**：第 3 次 **6/6** 说明判据能过、不是坏尺子
      → 省掉了原计划解 `demo_full.zip` 做参考实现对拍的步骤。

### 9/20 19:2x–19:5x —— ✅ **M3b-0 自动化闭环完成**（`docs/11-M3b0自动化闭环.md`）

- [x] **出口判据 #1**：**零人工介入下构建成功 且 6/6 通过**（地板 4/6）。
- [x] **出口判据 #2（held-out）**：换 `ticketbooking-demo` 的 REQ-1.2、**代码一行不改** →
      **零人工构建成功**（97 模块）。→ 闭环**不是**对 quickstart 的特异化。
- [x] 新增三个模块：`verify/l1.py`（**静态**闸门：import 解析 / 路由与入口 / 可访问名存在 /
      脚手架未改 / scripts 完整）、`verify/selfcheck.py`（模型自检 + 定向修复）、
      `verify/loop.py`（有界轮次编排，默认 3 轮）。`main.py` 接线（`PIPELINE_VERIFY` / `PIPELINE_REPAIR_ROUNDS`）。
- [x] **M3a 的四类手工修复全部机器化**（实测抓到并修掉）：import 路径错（L1）／上游模板 bug
      （`apply_known_fixes`）／`onRegistered` 没传 · 绕过 auth context · 用户名拼接（自检）。
- [x] **闭环第一版没收敛**，四处缺陷逐个修掉（详见 `docs/11` §三），其中最关键的一条是
      **允许修复一并新建缺失文件 + 给它已存在文件清单**——它补的是模型"爱 import 自己没产出的辅助文件"
      这个**系统性**弱点，不是样本补丁。
- [x] 成本：主跑 **92,158 token / 14 次调用**；held-out **78,821 / 16 次**。
      ⚠️ **跨网关比绝对值无意义**（M3a 用比赛网关 + `deepseek-v4-flash`，87% 是 reasoning；
      M3b-0 用 DeepSeek 官方 + `deepseek-chat`，该 API 不报 reasoning 字段）。

### 9/20 20:1x–20:4x —— ✅ **M3b-1 前置：模型/成本口径标定完成**（`docs/12-M3b1模型成本标定.md`）

- [x] **三个旋钮都测了**（同一样本 + 6 条判据，口径 = `token / 通过条数`）：

| 配置 | 网关 | token（含全部尝试） | 通过 | token/通过 |
|---|---|---:|---:|---:|
| `deepseek-chat`（默认） | DeepSeek 官方 | 92,158 | **6/6** | **15.4k** ✅ 最优 |
| `deepseek-v4-flash` + `reasoning_effort=low` | 比赛网关 | 81,862 | 5/6 | 16.4k ❌ |
| `glm-5.3-flash`（默认） | 比赛网关 | **53,723** | **0/6** | ∞ ❌ |

- [x] **旋钮 2 答"网关认 `reasoning_effort`"**：小样本上 reasoning 949→52（占比 77%→11%）、成本指数 −60%，
      且**正文没截断反而更长**。
- [x] 🔴 **但端到端把它否掉了**：`low` 少 11% token 却少过 1 条；`glm-5.3-flash` 最便宜（−42%）
      却 0/6（原因查清：入口对、能构建、L1 全绿，但**用户名渲染成拼接文本**
      → `getByText(exact)` 匹配不到，与 M3a 的 5.3c 同类）。
- [x] **这条标定的真正教训**：**小样本探针会把这两个错配置都判成"更优"且理由充分**——
      `PLAN.md` 坚持"端到端 + token/通过条数"是对的。**任何省 token 的旋钮都必须复测 6 条判据。**
- [x] **顺带暴露闭环短板**：自检对"显示形态不匹配"的把握**随模型而异**
      （deepseek-chat 抓到并修掉用户名拼接；glm 没抓到）→ 这类约束应进**生成 prompt 的硬性清单**，
      不能只指望事后自检。
- [x] 新增 `eval/m3b1_model_probe.py`（小样本筛选脚本）与 `llm.chat(extra=…)` /
      `PIPELINE_LLM_EXTRA` 全局旋钮口（平台上由代码默认值决定，不靠环境变量）。
- ⚠️ **一次测量事故**（记下来免得重犯）：配置 B 首次跑出 0/6，我先当成配置结论——
      其实是**后端 npm 依赖没装**（只装了前端），后端起不来（`GET /` → 000）。
      **教训：跑判据前必须先确认探活 200；等待循环超时了就该停下。**

### 工具链

- [x] `eval/bench.sh` —— 五个子命令（setup/start/stop/reset/test/status）；含**互斥锁**与**游离 runner 清理**
- [x] `eval/extract_run.py` —— 从日志自动抽 RunRecord（不再手写 50 个字段）
- [x] `eval/check_reqcompile.py` —— **第 1 阶段验收脚本**（全绿才继续）
- [x] `pipeline/arc_runtime/` —— 从 ARC 整包复制的 SDK
- [x] `pipeline/templates/web-react-express/` —— 模板已复制

### 管线第 1 阶段（需求编译，纯代码 / 0 LLM 调用）—— 已跑通

- [x] `pipeline/main.py` —— 入口。**CLI 参数与环境变量双支持**（`compile <path> -o <dir>`
      与 `ARCBENCH_OUTPUT_DIR` 等都能用），并把每个值的来源打进日志——
      一次真实提交就能顺带回答"平台到底怎么调的"
- [x] `pipeline/requirements.txt` —— 上游超集（"只增不减"）
- [x] `pipeline/reqcompile/loader.py` —— 加载 + 校验，**补上 ARC 全缺的四项**：
      重复 id、未知 dependency、依赖环、children 环
- [x] `pipeline/reqcompile/yamlrepair.py` —— **解析器驱动的缩进修复**（见下方阻塞项）
- [x] `pipeline/reqcompile/scenarios.py` —— 场景索引（测试标题靶子）
- [x] `pipeline/reqcompile/a11y.py` —— 可访问名抽取，**对 12306 覆盖 98%**（**窄口径**：分母是 57 个经 6 个 helper、以 `page` 为 scope 的调用；**宽口径 80%**，见 `docs/06` §三补）
- [x] 七个目标全部编译通过：6 个赛题 + quickstart（`eval/check_reqcompile.py` 全绿）

### 已修正的错误（来自一次外部审核）

- [x] `bench.sh` 只杀 npm 包装进程 → 改杀**进程树**并验证端口释放
- [x] `bench.sh` `-f` → `-d`
- [x] 显示过滤器会吃掉 `skipped` 行 → 导致分母记错（曾把 135 记成 132）
- [x] `docs/02` 说模板 `playwright.config.js` 写 3301 → 实际是 **3000**
- [x] `docs/02` 补 `ARC_AGENT_TEMPLATES_ROOT`（`base.py:18-23`）
- [x] `docs/02` 把 `Template directory not found` 的归属从"平台日志"改成 ARC 自身报错（`base.py:81`）
- [x] C1–C10 统一到 `docs/02` 为唯一来源（原先两处编号错位）
- [x] 数字口径实测校正：**460 ATOMIC / 460 spec / 478 tests**。
  ⚠️ **模块数逐个 app 与平台完全一致**（468 = 468）——不存在"版本差 +8 模块"，那是把平台的"全部叶子"和本地的"有场景叶子"当成同一个量了。**真正的差只有测试数 +6**（本地 478 / 平台 484，12306 占 +3）

---

## 进行中

无。

---

## 下一步

按优先级：

> **先看 `PLAN.md` §7 的阶段表**——阶段由那里定义，本栏只记进度（`AGENTS.md` 会话协议有判据）。

-1. **✅ M3a 能力探针已完成**（结果见「已完成」与 `docs/10-M3a能力探针.md`）
   **判读（按预登记的叉表）：命中第一行「机制成立」**——3/6（原始产物）→ **6/6（手工最小修复后）**。
   **所以 §2 不翻 ARC 主线**，自研继续。

0. **✅ M3b-0 已完成**（见「已完成」与 `docs/11`）：L1 闸门 + 模型自检 + 定向修复闭环，
   零人工 6/6、held-out 零人工构建成功。**下一步转 M3b-1**（`PLAN.md` §7）：
   先做**模型/成本口径标定**（抑制 reasoning 的旋钮实验），再把通过率铺到 6 个任务。

0b. **🔴 补 a11y 第 5 条规则：散文名词枚举**（**已裁定批准，附三个约束** —— `PLAN.md` §4.1）
   ⚠️ **它是 M3a 的下游，不是前置**：M3a 用的 quickstart 需求自带可访问名契约，不需要抽取器。
   把无引号名词短语提成必做清单，但**位置约束**（只抽祈使宾语位：`Display X, Y, Z` /
   `Click the X link` / `fill in the X field`），并让 L1 按 app 分两类判据。
   **理由**：L1 现在对 prestashop(50%) / keep(40%) / stackoverflow(29%) / ctrip(18%) 的
：散文名词枚举**（**已裁定批准，附三个约束** —— `PLAN.md` §4.1）
   ⚠️ **它是 M3a 的下游，不是前置**：M3a 用的 quickstart 需求自带可访问名契约，不需要抽取器。
   把无引号名词短语提成必做清单，但**位置约束**（只抽祈使宾语位：`Display X, Y, Z` /
   `Click the X link` / `fill in the X field`），并让 L1 按 app 分两类判据。
   **理由**：L1 现在对 prestashop(50%) / keep(40%) / stackoverflow(29%) / ctrip(18%) 的
   **交互单元**无事可查，表现为"闸门报绿而外部测试大面积失败"。
   **三个约束（缺一不可，否则会顶到 `pass/CNY`）**：
   ① 位置约束，不做无差别名词枚举；② **必须同时报精度**（每 app 抽 20 条人工判"像不像可交互对象"）；
   ③ 散文式 app 的 L1 先 **fail-soft**（告警进 `ValidationReport`，不判死）。
   **验收**：交互单元空转率下降（基线见上），**且**精度抽样过关。
   **它是批量生成阶段的前置**（`PLAN.md §4.1` 原文："这件事应该在批量生成之前做"）。

1. **✅ 让生成阶段产出功能 —— M3a 已达成**（原始 3/6 → 修复后 6/6）。
   它的三个失败根因已转成第 0 项（L1 闸门）。

2. **🟡 让 ARC 真的 `compile` 跑一次**（`R5-收口` ③；如果保底改走自研管线，这条降级为"备选路线验证"）
   前两项（安装/导入/体检、`pip freeze` 存档）都已完成；**③ 从未做过**，
   而 **9/23 的保底包就是 ARC**——在它跑通之前，"保底包能上榜"只是假设。
   ⚠️ 要真实 LLM 调用（会花 token）；用最小的 `example/ticketbooking-quickstart`（单 REQ-1）。
   ⚠️ ARC 的 `.env` 只找**当前目录**，本地跑要显式加载工作区 `.env`（或用 `.venv`）。

3. **✅ `eval/bench.sh` 防事故 5 条 + 死锁出口 `recover`**（已完成并验证，见「已完成」）。

4. **✅ V5 — 已完成**（见「已完成」；平台怎么调我们**已成事实**）
   - 提交包 `output/bundle.zip`（`./eval/package.sh` 构建 + 合规校验全绿）
   - 操作与平台事实：`docs/08-V5-提交操作指导.md`；结论见该文件「V5 结论」一节
   - ⚠️ 提交需要 **ZIP 文件上传**，浏览器自动化**明确不支持上传**（`capability_unsupported`）
     → 这一步只能由人来点（已完成两轮：`probe-v1-doctor` / `probe-v2-scaffold`）

5. **V6 — 在 `bbs.qiwoo.edu.cn/t/topic/16` 问死三个问题**
   - 单次提交的**墙钟上限**是多少？
   - **token 配额上限**是多少？
   - 主赛道 6 个任务能否**分次提交累积**完成？

6. **搭 `pipeline/` 骨架**（不需要账号，可并行开始）
   - [x] `main.py` + `requirements.txt`（入口契约）
   - [x] `reqcompile/` —— 需求解析。**必须容错**：6 个需求文件有 2 个 YAML 无效（见下）
   - [ ] **墙钟预算与降级模式要从第一版就做进去**（`PLAN.md` §4.3），不是事后优化

7. **✅ 打通最小闭环 —— M3a 已达成**：quickstart（单 REQ-1）→ 生成 → 起服务 → 跑 Lab04 的 6 条测试
   ⚠️ **口径必须三个数一起引用**：**原始产物 0/6（构建失败）→ 1 处人工修构建后 3/6 → 3 处人工接线修复后 6/6**
   —— **平台上没有人在回路里**，所以"零人工"才是我们的真实水平；**6/6 是手工修复后的读数**，
   它证明"判据能过、机制成立"，**不证明当前能交付**。详见 `PLAN.md` §7 M3a 与 `docs/10`。
   - [x] 第 1 阶段：需求编译（`pipeline/main.py compile <path> -o <dir>`）
   - [ ] 第 2–5 阶段：设计 → 生成 → 验证
   - [ ] 🔴 **零人工介入下构建成功**（M3b 的第一条闸门，**目前不成立**）

（原第 6 项已上移到第 1 项）
   `arc doctor` 已通过（exit 0），但 **`arc compile` 从未跑过**——9/23 的保底包就是「ARC + 输入修复壳」。
   ⚠️ 这一步**需要真实 LLM 调用**（会花 token），且 ARC 的 `.env` 只找**当前目录**，
   本地跑要显式加载工作区 `.env`。**含义**：在它跑通之前，"保底包能上榜"仍只是假设。
   建议用最小的 `example/ticketbooking-quickstart`（单 REQ-1）试跑。

7. ~~**建 venv + 装 ARC 依赖跑一次**（把 R5 从"未知"变"已知"）~~ → ✅ **已完成（2026-09-20 12:0x）**，见「已完成」

---

## ✅ 已裁定：闸门设计改动（2026-09-20，方案会话）

**问题**：`PLAN.md §4.2` 的 L1 闸门（"需求声明的可访问名都存在"）**只对 12306 有效**。
prestashop / keep / stackoverflow / ctrip 的多数交互单元抽不到任何可访问名，L1 对它们**报绿而无信息量**。

**裁定：批准**补第 5 条规则（散文名词枚举）+ L1 按 app 分两类判据，**附三个约束**（位置约束 / 同报精度 / 先 fail-soft）。
验收指标改用**交互单元空转率**（基线已实测）。**全文与理由见 `PLAN.md` §4.1 的「✅ 裁定」一节**，
执行入口见上面「下一步」第 0 条。

> 这条改动原先挂在"待裁定"栏；第五轮审核建议批准，方案会话已落地裁定文本。

---

## 阻塞项


| 项 | 阻塞什么 | 谁来解决 |
|---|---|---|
| ~~ARC-Bench 平台账号是否已注册~~ | — | ✅ **已确认（2026-09-20，用户）** |
| ~~官网报名是否确实完成~~（9/7 截止） | — | ✅ **已确认（2026-09-20，用户）** |
| ~~首次提交的文件上传~~ | — | ✅ **已完成两轮**（`probe-v1-doctor` / `probe-v2-scaffold`，V5 结论见 `docs/08`） |
| 单次墙钟 / token 配额上限 | 排期与决策门（9/23） | V6 提问后可知 |

---

## ⚠️ 新发现的阻塞：2 个需求文件 YAML 无效

2026-09-20 实测：`arc-bench/webapp/<app>/requirements/requirements.yaml` 里 **bookstack 和 keep 两个文件的 YAML 语法无效**，`yaml.safe_load` 直接抛 `ScannerError`（单个键相对兄弟键过度缩进，共 3 行）。

- **ARC 用的就是 `yaml.safe_load`**（`core/files.py:14`）→ 喂给它这两个 app 会**硬失败**
- 这两个恰好是 **`arc-bench-lite/` 的两个 app**，也是原方案建议"起步用"的两个
- **影响**：任何阶段一"从最小 app 起步"的计划都要改成 **`stackoverflow`(66) 或 `prestashop`(86)**
- **对管线的要求：需求解析器必须容错，不能硬失败。** 细节见 `docs/03` §5.5

**✅ 已实现（2026-09-20）：`pipeline/reqcompile/yamlrepair.py`**

做法是**解析器驱动的搜索**，不是手写缩进启发式：解析失败 → 取错误行号 →
从"附近真实出现过的缩进值"里挑候选 → 逐个试，**只接受让错误位置严格后退
或让文件通过的那个改动** → 改不动就带诊断放弃，绝不产出半修好的树。

实测：bookstack 修 2 处（均 20→16），keep 修 1 处（12→8），两份都能解析
（节点数 56 / 45）。每处修复都写进 `ValidationReport.repairs` 并打进日志留痕。

→ 详见 `docs/06-需求契约实测.md` §一（含"为什么这条足以让榜单分不成立"）

---

## 最近实验结果

### 12306 参考实现的五轮干净测量（2026-09-20）—— **R5 满分，两条旧结论被推翻**

五轮都是**每轮前重置数据库 + 重启后端**，产物冻结、无代码变化：

**🔴 必须分组读（混口径会算错跨度）** —— 权威解读：`runs/20260920T-quiet-seq-README.md`

| 条件组 | 轮次 | 日志 | 通过 | 失败 |
|---|---|---|---|---|
| **污染**（并发事故窗口 10:32–11:10） | R1 | `20260920T023216Z-12306-round1.log` | 129 | 6 |
| 污染 | R2 | `20260920T030002Z-12306-round1.log` | 130 | 5 |
| 污染 | R3 | `20260920T030533Z-12306-round2.log` | 130 | 5 |
| 污染 | R4 | `20260920T031013Z-12306-round3.log` | 133 | 2 |
| **静置** | R5 | `20260920T043334Z-12306-round1.log` | **135** | **0** |
| **静置**（`arm=refimpl-quiet-3r`） | Q1 | `20260920T053649Z-12306-round1.log` | 134 | **1** |
| 静置 | Q2 | `20260920T054008Z-12306-round2.log` | 135 | **0** |
| 静置 | Q3 | `20260920T054320Z-12306-round3.log` | 133 | **2** |

**分组跨度：污染 2–6（跨度 4）／静置 0–2（跨度 2）。**
**"跨度 6"是把两组混算出来的假象——已作废。**

**R5 = 135/135，3.0 分钟**（`arm_id=refimpl-baseline-r5`；也是五轮里最快的——
没有超时等待就没有等待时间）。**失败数在 0–6 之间摆动。**

🔴 **R5 推翻了先前两条结论**（改的是事实，判据改动已按规则挂起）：
1. ~~**`REQ-4.3.7` 稳定失败**（四轮全挂）~~ → **它通过了。零稳定失败。**
2. ~~**理论天花板 134/135**~~ → **作废**。实测区间 **129–135**，满分**可达但不稳定**（五轮一次）。

- **会翻面的测试共 11 条**（完整清单见 `docs/04` §三）——R5 里这 11 条**全部通过**
- **稳定通过 124 条 + 翻面 11 条 = 135，零稳定失败**
- → 目标设定仍是"**不追 100%**"，但理由从"满分**不存在**"改成"满分**不稳定**"。
  **不要再引用"天花板 134"**——那会让真实可达的 135 被当成不可能。
- → 先前记的"132/132 干净库"过去被当"运气好"排除，R5 之后**不能再一句话排除**。
- ✅ **门槛重推已完成**（`PLAN.md` §6 裁定）：**静置口径**下可辨差异 **2 条**、**保留门槛 4 条**；
  **只在静置条件（`load_snapshot` 干净）下比较**，含污染轮次的比较一律无效。
  自我修正：静置样本仅 4 轮，**跨度涨到 ≥3 → 门槛退回 6；≥4 → 退回 8**。
- ✅ **`CI=1` 重试实验：结论是不开**——静置抖动本来就接近 0，开重试是改口径而不是压噪。
  改为本地固定 `retries=0` + 把 `ci_env` 记进 RunRecord。

### 其它基线

| 指标 | 值 |
|---|---|
| **12306 套件规模** | **135 条测试 / 117 个 spec 文件**（`playwright --list` 权威） |
| 参考实现通过率 | 实测区间 **129–135 / 135**（五轮；R5 = **135/135**） |
| 单轮耗时 | 3.0 – 8.0 分钟（12306 单 app；满分那轮最快） |
| 失败形态 | 主要是 `toBeVisible()` / `toBeChecked()` 超时——**交互时序问题，不是功能缺失** |

**跨 app 外推**：公开快照六个 app 合计 **478 条测试**（平台标注 484）。
纯测试时间估 15–30 分钟，**不含生成时间**。

**已作废的 run**：`runs/*.INVALID-concurrent.*` 两个 —— 并发运行互相污染
（详见 `AGENTS.md` 与 `docs/04` §2）。

---

## 未决问题

- **L1 闸门对散文式需求的判据**（交互单元空转 50%/40%/29%/18%）—— **已裁定批准**，见「✅ 已裁定」
- ~~**噪声门槛该取多少？**~~ → ✅ **已解决**：静置序列已跑（`arm=refimpl-quiet-3r`），
  **混口径的"跨度 6"被证明是假象**（静置 0–2 / 污染 2–6），门槛重推为 **4 条**（静置口径，附自我修正规则）。
  见「最近实验结果」与 `runs/20260920T-quiet-seq-README.md`。
- ~~`frontend/dist/index.html` 由谁构建？~~ → ✅ **已解决：平台自己构建**
  （V5 日志：`Building template frontend → Template frontend built`，frontend npm install 290 包 / 16s）
- 🔴 **榜单前列的 token 异常**：前 6 名里多名队伍 **token ≈ 0 / runtime ≈ 0 却拿到 80–100% 通过率**。
  要么他们的应用不是现场用 LLM 生成的（撞"不得预埋答案"红线），要么**计量口径有我们还没理解的机制**。
  **在搞清之前不要据此调整策略**——但它可能直接决定 `pass/CNY` 怎么算才划算。
- 视觉模型用 `deepseek-v4-flash-vision-exp` 还是干脆关掉视觉分析？（方案最初倾向关掉——判分是行为测试不是像素比对）
- reasoning token 能否用参数抑制？还是模型固有？（影响批量生成成本）
- **场景探针到底值不值**？第一轮实验要 A/B："有探针" vs "无探针（只有结构反馈）"，把探针成本计入 `pass/CNY` 分母
- 那两个损坏的 YAML：是公开快照独有，还是平台线上也坏？（若线上也坏，则 6 个 app 里有 2 个对**所有**队伍都不可做，这反而是相对优势）
- **夹具抽取器还没写。** `docs/02` §4 说夹具漏播会大面积假失败且与功能质量无关，是最高优先级的低成本项。
  12306 的夹具在需求里是英文自然语言（*The system contains the verified account registered_user with
  username "registered_user" ... and password "Password123!"*），与测试侧 `FIXTURES` 逐字段一致——
  抽取器要能从这里生成 `seed_db.js`。
- ~~`REQ-4.3.7` 为什么四轮全挂？~~ → ✅ **已解决：R5 它通过了**，所以不是参考实现的 bug，
  也不是稳定失败——是**时序噪声**这一类的翻面项。**134/135 不是硬上界。**（`docs/04` §三）
- **噪声跨度由 4 变 6 之后，门槛该取多少？**（"可辨差异 4 条""保留 ≥8 = 2×跨度"）
  属**测量方法**改动 → 待检查点/审核裁定；裁定前 4–6 条的差异不下结论。

**已解决**：
- ~~`pipeline/` 用 Python 还是 TypeScript？~~ → **已定 Python**，第 1 阶段已实现并验收
- ~~测试套件的翻面条目是哪几条？~~ → **已定位 11 条，零稳定失败**（R5 修正后），见 `docs/04` §三
- ~~需求解析器如何容错坏 YAML？~~ → **已实现** `yamlrepair.py`，见上

---

## 第二轮审核遗留（**执行层**交接；方案层已全部处理）

方案层（`PLAN.md` / `AGENTS.md` / `runs/README.md`）已按审核改完。以下是需要在**工具与代码**里落地的：

### ✅ 并发安全 —— **5 条全部落地（2026-09-20 12:2x–12:4x，方案会话转执行时做的）**

1. ✅ **`start` / `stop` 也走锁**（`cmd_start` / `cmd_stop` 先 `acquire_lock`）。已实测：
   有活锁时 `stop` 被拒（`拒绝：检测到正在运行的测量 —— 锁持有者 PID=… 还活着`）。
2. ✅ **`status` 改出一条结论行**，锚点是锁（`test/reset/start/stop` 全都拿锁 → "没锁"就等于"没人跑"）。
   六种现场各有明确结论，见 `docs/04` §2 的表。**已删除"跑 reset 清理"那句反向建议。**
3. ✅ **过期清理改四条件**：PID 已死 **且** 无 runner **且** 端口空闲 **且** 锁年龄 > 阈值。
   阈值 `BENCH_LOCK_STALE_SECS` **默认 7200 秒**（不是 30 分钟）；不满足就拒绝并打印"需人工确认"。
4. ✅ **锁加心跳**：`runs/.bench.lock/heartbeat` 每 20 秒更新；判"是否在跑"**优先看心跳**，不看年龄。
   持有者进程消失时心跳自己退出（避免幽灵新鲜锁）。
   实测：R5 测量期间该栏正确显示 `test 12306×1 轮（arm=refimpl-baseline-r5…）`，结束后自动清除。

**验证方式（六类现场全测过）**：
- **真实测量进行时**（R5）：`status` → `🟢 正在跑，不要动`（证据含端口占用 + 3 个 runner + 锁持有者活着）；
  另一进程抢锁被拒，原锁与心跳不受影响 ← **这就是那两次 run 作废的事故场景，现在被挡住了**
- 隔离用例（`BENCH_LOCK_DIR` 指向临时目录，不碰真实锁）：死 PID+心跳新鲜 → 拒；死 PID+锁年龄 30s → 拒；
  死 PID+无心跳+锁年龄 3h → 清；活 PID → 拒
- 结论行五现场：空闲 / 残留后端 / 正在跑 / 过期锁 / 不确定
- 认领行"写入-清除"往返后 `STATUS.md` **逐字节还原**（sha256 比对）

**顺带修的两个操作性问题**：
- `nohup` 起后端时补 `</dev/null`，并把心跳子壳的三个 fd 全部脱离调用者——
  否则 `bench.sh start | tail` 这类**带管道的调用永远不返回**。
  ⚠️ 但 Windows 下 detached 进程仍会继承**管道句柄**（MSYS 的 fd 重定向管不了 OS 句柄），
  所以"带管道调用看起来卡住"仍可能发生——**它不是故障，别强杀**（见 `docs/04` §2 末尾的说明）。
- `cmd_reset` 被 `cmd_test` 调用时不再覆盖认领行的描述（整轮测量期间应显示 `test …` 而不是 `reset …`）。

### 🟡 记录完整性

6. **`extract_run.py` 只记失败清单**，`skipped`/`flaky` 只记计数。应把具体条目标识也记下来，否则带跳过的对比无法从计数反推。
7. **`ARC_TEST_DATE` 工具没强制**：三份记录的 `arc_test_date` 都是 `null`（`extract_run.py` 留空、`bench.sh` 没传）。硬规则说"固定 `ARC_TEST_DATE`"，但工具没强制 → 记录无法自证。今天全在 UTC 9/20 内所以数据不算废，但必须补上。
8. **`git init` 仍未做** → RunRecord 的身份字段只能填 `null`，且"一次只改一处"无法审计、坏改动无法回退。

### 🟢 便宜的压噪实验（建议优先做）

9. **`CI=1` 重试实验。** `playwright.config.ts` 写的是 `retries: process.env.CI ? 1 : 0`，而 `run-playwright.js` 用 `...process.env` 透传、自己不设 `CI`、只强制 `--workers 1`。
   所以**本地跑是 `retries=0`**——每一次时序抖动都被记成失败；平台若设了 `CI=1`，则有一次重试，抖动会记成 `flaky` 而非 `failed`。
   → 跑一次 `CI=1 ./eval/bench.sh test 12306 3`，对比翻面数。**若从 5–8 降到 1–2，就应该把"测量是否开重试"当固定口径写进 `docs/04`**（并注明它与平台口径的关系）。
   **这可能是把信噪比提高数倍的最低成本动作。**

### 🟢 可访问名抽检（9/20 12:0x 方案层新增，交接给执行）

13. **把探针折进 `eval/check_reqcompile.py` 作为检查 4。**
    - 现成实现：`docs/06-a11y-recall-probe.py`（**审计快照，折进去之后可删**）
    - 输出基线：`runs/20260920T-interaction-unit-a11y.txt`（**当前口径**，含交互单元）
      + `runs/20260920T-a11y-recall-all-apps.txt`（收窄前的版本，保留供对照）
    - ⚠️ **验收口径 = 交互单元空转率**（`PLAN.md` §4.1 裁定）：
      分母只算**场景名或步骤里含 `click / select / enter / check / submit` 的单元**。
      **基线（不得变差）**：`12306 2% · ctrip 18% · bookstack 19% · stackoverflow 29% · keep 40% · prestashop 50%`
      （分母大小：99 / 112 / 32 / 49 / 25 / 66）。也可同时报全部单元口径作对照：
      `5% / 26% / 24% / 44% / 50% / 62%`。
    - ⚠️ **判据必须吃词形变化**（`clicks` / `enters` / `submitted`…）。
      第一版写成 `\bclick\b` 时，bookstack 的交互单元从 32 个掉到 6 个、空转率虚高到 83%。
      **与 `docs/06` §三 记过的 `\btab\bs?` 是同一个坑。**
    - ⚠️ 折进去时不要照抄"引号名 98%"那套旧口径：**分母必须含正则字面量与全部名字位置 helper**，
      否则 prestashop / stackoverflow / ctrip / keep 会被误判成"测试不用名字"（实测这四个 app 的测试**零引号名**，全用 `/x/i`）。

### 📌 待用户输入（唯一剩下的）

10. ~~ARC-Bench 平台账号是否已注册~~ → ✅ **已确认（2026-09-20，用户）**
11. ~~官网报名是否确实完成~~（9/07 截止）→ ✅ **已确认（2026-09-20，用户）**
12. **平台登录态** —— 账号存在，但会话里没有登录凭据。**V5 只剩这一个前置**：
    给登录方式，或由用户自己点提交。

**V5 可以省一步**：`pipeline/main.py doctor` 会把 `ARCBENCH_*` 变量与每个参数的来源全打出来，
所以**直接用现有包提交一次就能回答"平台怎么调我们的"** ——
**探针 bundle 只在对包体有干扰时才有必要。**
