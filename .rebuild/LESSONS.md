# LESSONS.md — 旧管线（GOSIM Create 初赛期）的血的教训

> **读者**：从零重写 `pipeline/` 的 agent。**这不是复盘，是设计输入。**
> **口径约定**（全文遵守）：
> - **「零人工」** = 生成之后没有人手改过产物一个字符。凡手工补属性/改接线，读数一律标 **非人工**，不得进闸门判据（`AGENTS.md` 硬规则 13）。
> - **「子集」** = 生成时用 `PIPELINE_REQ_IDS` 只喂 N 条需求；**「全量」** = 喂整份需求文本。
>   判分侧一律是**该 app 的全量套件**（分母 = 该 app 全部 spec），这一点在 §6 反复标注。
> - 每条结论都带 `文件:行号`。行号以当前工作区文件为准，文件被改过就不再成立。
> - 旧代码只当零件库；本文档不代表旧设计正确。

---

## 1. 平台硬事实（逐条，有出处）

**1.1 平台怎么调用入口**
- `docs/09:122-123`、`docs/13:115-116` 原文：`python3 /workspace/submission/main.py <需求目录> --output-dir /workspace/template`
  → 需求是**位置参数且是目录**（不是文件）；输出是 **`--output-dir` 长选项**。两者都必须支持，且把**参数来源**打进日志。
- `docs/09:104-116` runner spec 原文：`submission_dir=/workspace/submission`、`template_dir=project_dir=output_dir=/workspace/template`、
  `tests_dir=/workspace/tests`、`arc_dir=.arc`、`task={category: web, test_runner: playwright}`。
- `docs/09:129`：我们的 argparse **同时支持 `-o` 与 `--output-dir`**，且打出来源——一次真实提交顺带回答了"平台怎么调我们"。

**1.2 注入的环境变量**
- `docs/09:131`：`MODEL` / `VISUAL_MODEL` / `ARCBENCH_RUNNER_EVENTS_PATH` / `ARCBENCH_TRACEABILITY_DIR`。
- `docs/02:36-38`：契约变量全集 `ARCBENCH_OUTPUT_DIR / ARCBENCH_PROJECT_DIR / ARCBENCH_TEMPLATE_DIR / ARCBENCH_RUNNER_EVENTS_PATH / ARCBENCH_TRACEABILITY_DIR`。
  `ARCBENCH_*` 都指向 `/workspace/template` 下面的 `.arc/`。
- `docs/13:88`：**API Key 是提交表单上的必填项，由我们自己提供** → 计量挂在我们交的 key 上。
- 🔴 `PLAN.md:2220`（A1）：**"平台容器里能调到模型（有凭据）从未验证"**——注入清单里 **没有 API key**，
  而**此前三次提交全是零 LLM 调用**（`PLAN.md:1974`）。**这是全项目最大单点风险**：若凭据不在，
  任何"现场用 LLM 生成"的管线在平台上会死在第一次调用上。

**1.3 输出目录与"平台自己干活"的部分**
- `docs/09:130`：应用写 `/workspace/template`；`.arc/` 在其下。
- `docs/09:148`：平台自己 `npm install`（前端 **290 包 / 16 秒**）→ `npm run build` → 后端 install → `npm run start` → **3000 端口可达**。
- `docs/09:150-152`：**`frontend/dist/index.html` 由平台构建**；**agent 里不要 npm install / build**——agent 跑起来之后没有网络。
- `docs/09:143-144`、`PLAN.md:19-22`：**平台自带模板 `/opt/arcbench/templates/web-react-express`**。
  查找优先级 `ARC_AGENT_TEMPLATES_ROOT` → `ARCBENCH_TEMPLATE_DIR` → 包内 `templates/` **正确命中了平台那份**。
- ⚠️ `docs/02:29`：云端**先按 `requirements.txt` 装依赖（这一阶段有网络）**，agent 真正开始跑之后**没有网络**。
  ⚠️ `PLAN.md:1884-1889`：**"agent 无网络"至今只有间接证据**（讲师的一句预期），不是实测。

**1.4 运行时**
- `docs/09:132`：容器里是 **Python 3.12**（本地 3.13）→ 不要用 3.13 专属特性。
- `docs/09:133`：pip 走清华镜像；npm 用容器内配置。

**1.5 第一道闸（per-run，不是打包校验）**
- `docs/09:29-32` 报错原文：`Your run failed: web template is incomplete: expected frontend/ and backend/ directories`。
- `docs/09:34-43` 诊断：它检查的是**"这次 run 产出了什么"**，不是"zip 里有什么"。
  证据：① 报错是 per-run 的 `Your run failed`；② ARC 正常流程第一步就 `copy_template()`；③ 我们当时只写事件 + traceability，**从不拷模板**。
- `docs/09:20-21` 启发式：**六个任务报同一个错 → 查公共环节**（骨架 / 构建 / 起服务 / 入口契约），**别去翻某个 app 的需求**。两次提交都是这个形态。
- 落地逻辑在 `pipeline/generate/scaffold.py`（`scaffold_app()` 把模板**内容**拷进输出根，`scaffold.py:104-133`）；找不到模板必须抛 `ScaffoldError` 并写 run-failed 事件，**不许静默跳过**（`scaffold.py:24-25`）。

**1.6 计分规则与"0 通过 = 不上榜"**
- `PLAN.md:35` 平台原文：*A complete leaderboard score is calculated only when one submission has a completed run for every task in this competition.*
  → **主赛道计分要求同一份提交跑完全部 6 个任务**（`docs/08:115-116`）。
- 🔴 `PLAN.md:570` 原文：**"0 通过 = 不上榜 = 等于没提交"**；`PLAN.md:572`：**"地板不是'能跑完'，是'至少有一部分功能可用'"**。
  实测：提交二让 6 个任务的 86 条测试**全部跑起来**、平台每一步都成功，但 `passed=0, failed=86, score=0.0` → **六任务各 0.0% → 不在榜上**（`docs/09:90-93`、`docs/09:18`）。
- `PLAN.md:47-48`、`docs/09:168-169`：榜单两级——**Senior = 平均通过率 ≥80%**（展示全部指标）/ **Junior = <80%**（只展示排名+用户+通过率+覆盖度）。**进 Junior 就是"有分"。**
- ⚠️ `PLAN.md:19-22`：**80% 不是排名门槛，只是展示档**（榜上前 6 名 82.1–100.0）。Top 20 的现实门槛是"名次靠前需要高通过率"。
- `PLAN.md:51` 目标函数原文：**`通过率 / 人民币`**；榜上同样 100% 通过率 Efficiency 从 5.42 到 7,692,307 差五个数量级，**唯一变量是 token**。

**1.7 token 预算与计量口径**
- `PLAN.md:638`：从第一天起 usage 落盘 `.arc/metrics.jsonl`，字段 `{ts, stage, node_id, model, input_tokens, output_tokens}`（旧实现 `pipeline/generate/llm.py:223-247` 另加 `run_id/call_index/reasoning_tokens/elapsed_s`）。
- `PLAN.md:640` + `PLAN.md:2172-2182`：**三个数一起记** —— `tokens_success_only` / `tokens_all_attempts` / `cny_all_attempts`；**分母必须包含失败 run 的消耗**。
  🔴 同一句"只回两个字"：**比赛网关计 97 token / DeepSeek 官方 API 计 10**（**十倍口径差**）→ `metrics.jsonl` 必须带 `endpoint`。
- `PLAN.md:2160-2161`：**成本主指标是 `token / 通过条数`，不是 token 绝对值**，否则"省 token 换掉通过率"会被记成收益。
- `PLAN.md:642-659`（R9）：**reasoning 占输出 86–93%，按不可优化项处理，所有预算按此算**（网关计费包含 reasoning，`docs/13:147-148`）。
- 旧实现里与预算有关的常量（可当反面教材）：
  `implement.py:119` `TOKEN_BUDGET_DEFAULT=300_000`；`implement.py:134-137` `BUDGET_BASE=560_000 / PER_CHAR=8 / MIN=300_000 / MAX=1_200_000`；
  公式 `clamp(560_000 + 8×brief字符, 300k, 1.2M)`（`implement.py:140-152`）。旧表的锚点是 quickstart 单需求 **20.7 万 token**（`PLAN.md:1798-1817`）。
- `PLAN.md:2207`（R4）：**单次提交的墙钟上限与 token 配额上限至今未知**（V6 没问）。
  侧面收窄：榜上有 `813.55M token / 379m` 的 run 跑完了 → **token 配额基本不是约束，墙钟才是**（`PLAN.md:537-538`、`docs/09:175-180`）。
- ⚠️ `PLAN.md:2214`（R10）：**榜前 6 名里有多个队伍 `token≈0 / runtime≈0` 却拿 80–100%**——计量口径存在我们还不理解的机制。
  **在搞清之前不要按榜单调整成本策略。**
- ⚠️ **证据链提示**：`PLAN.md` 全文**没有 `2026-09-26`、也没有大写 `FAILED` 的提交记录**（两次 grep 均 0 命中）——
  09-26 那次提交只留下 `bundle.zip = 90180c3`（`STATUS.md:239`、`docs/08:21`）。**别指望方案正文里有这条**：
  它的机制证据是 `PLAN.md:593-599` + `PLAN.md:1910-1917`（事前的预测）+ §5.1 的复算（实测）。

**1.8 判分墙钟（决定排期，也决定"长等待"是常态）**
- `PLAN.md:511-547`：`playwright.config.ts` 写死 `timeout=60_000` + `workers:1` + `fullyParallel:false`
  → **判分最坏 = 判据条数 × 60 秒**；六个 app 合计 **478 条 ≈ 8.0 小时**。
- 同处判据推论：**通过的那条只花 0.3–1.5 秒，失败的花满 60 秒** → **"通过数翻倍 ≈ 判分时间减半"**，提高通过率同时是"跑得完"的杠杆。
- 实测（`docs/13`）：keep 32 条 **28.1–30.7 分钟**；bookstack 34 条 **34–35 分钟**；stackoverflow 66 条 **1.0 小时**；
  prestashop 86 条 **1.4–1.5 小时**；ctrip 125 条 **1.4–1.5 小时**；12306 135 条 **2.4 小时**；参考实现 12306 **3.0 分钟**（全绿所以快）。

---

## 2. 必须遵守的红线（每条给出处）

| # | 红线 | 出处 |
|---|---|---|
| 1 | **zip 第一层必须直接是 `main.py`**（不是 `src/main.py`、不套目录）。生成方式：**在源码目录内部**执行 `zip -r ../bundle.zip .` | `docs/02:19-20`、`PLAN.md:151`（讲师点名的第一大坑）、`eval/package.sh:40-42` |
| 2 | **包根层不能有 `package.json` / `index.js` / `index.ts`**——有的话 runner 会切到 Node 入口路径（平台文档：*When package.json exists, the runner installs Node dependencies before invoking the agent*）。⚠️ **只管根层**：`templates/.../backend/package.json` 这类子目录的**必须存在**，不违规；检查用 `unzip -l \| grep -E '^[^/]+/(package\.json\|index\.[jt]s)$'`，**不要全仓 grep** | `docs/02:21`、`PLAN.md:152-155`、`eval/package.sh:60-64` |
| 3 | **不塞 `.env`**（bundle 里 `.env` 的加载路径会指向上一级；且平台注入的环境变量优先级更高） | `docs/02:22`、`PLAN.md:156` |
| 4 | **不塞** `.git/ node_modules/ __pycache__/ *.egg-info/ *.db .arc/ workspace/ output/` | `docs/02:23`、`eval/package.sh:43-48` |
| 5 | **不得预埋答案**：模板只放基础设施、**零业务语义**；"全仓"应读作"**提交包**"（作用域 = `eval/package.sh` 打进 zip 的东西 = `pipeline/` + `templates/`）。**`docs/` 不提交，可以且应该继续用样本名做实测记录** | `AGENTS.md` 红线 9、`PLAN.md:2212`、`docs/06:290-293` |
| 6 | 判据已机器化：`python eval/check_bundle_strings.py --zip output/bundle.zip`，`package.sh` **每次打包前都跑**（`package.sh:84`）。实测它**当场抓到** 4 处新注释里的 app 名 | `docs/08:18`、`docs/08:37-38` |
| 7 | **API key 不写进任何会被提交或被引用的文档**；凭据只从环境变量/工作区 `.env` 取 | `AGENTS.md` 红线 10、`docs/08:142-143` |
| 8 | 必须用 `arcbench_agent_runtime` 写 `.arc/runner-events.jsonl` 与 `.arc/traceability/*.json`；**不得手工构造事件 payload** | `PLAN.md:157` |
| 9 | **模板里零业务语义**；"业务无关但题目可能用到"的通用组件也算擦边，**一律不做**（灰色地带） | `docs/02:227-229` |
| 10 | **不修改 `repos/`**（上游只读参考） | `AGENTS.md` 红线 6 |

**生成的应用必须满足的契约（C1–C10，唯一来源 `docs/02:66-85`）**：
C1 输出目录必须有 `frontend/` + `backend/`（**agent 负责**，平台硬报错）；
C2/C3 前端 build 与 `frontend/dist/index.html`（**平台负责**）；
C4 `backend/package.json` 必须有 `scripts.start`（模板已有，`docs/02:141-144`）；
C5 `scripts["db:prepare:e2e"]`（**待确认**是否被平台调用，`docs/02:80`）；
C6 后端启动 20 秒内端口可连（平台）；C7 单端口、后端托管 `frontend/dist`；C8 端口读环境变量不硬编码；
C9 `playwright.config` 读 `PLAYWRIGHT_BASE_URL`；C10 sqlite 文件 + 支持 `ARC_DB_FILE`（**待确认**）。
⚠️ `PLAN.md:163`：**违反契约会静默导致 E2E 全挂，不给明确报错。**

---

## 3. 已实测证伪的假设（旧说法 → 实测事实 → 出处）

1. **"只跑第 1 阶段（需求编译）也能交差"** → 平台第一道闸就挂，六任务全挂 → `docs/09:29-43`。
2. **"测试全红没关系，先跑完"** → **0 通过 = 不上榜 = 与没提交等价**；地板是"至少有一部分功能可用" → `PLAN.md:567-572`、`docs/09:18`。
3. **"平台线上任务包和公开快照差很多（版本差 +8 模块）"** → 逐 app **模块数完全一致**（468=468），**只差测试数 +6（本地 478 / 平台 484）** → `PLAN.md:2205`、`docs/13:91`。
4. **"6/6 是零人工的能力读数"** → 两次 6/6 **都不是零人工**（一次手工修 3 处接线，一次手工补一个 `aria-label`）；**零人工最好读数 = 5/6**；
   唯一零人工 6/6 是 quickstart（单需求）在 `deepseek-chat` 上、且**平台模型未复现** → `AGENTS.md` 规则 13、`docs/13:138-140,317`、`PLAN.md:876,1090`
5. **"换成平台注入的模型（`deepseek-v4-flash`）结论不变"** → 同需求/同判据/同管线只换模型：**6/6 → 3/6**，token 92,158 → **156,343**（reasoning 占输出 86%）→ `docs/12:155-177`、`PLAN.md:2112-2118`
6. **"省 token 的旋钮（`reasoning_effort=low`、换便宜模型）都能保通过率"** → `low` 少 11% token 却**少过 1 条**；`glm-5.3-flash` 便宜 42% 却 **0/6**。**小样本探针会把这两个错配置都判成"更优"且理由充分** → `docs/12:40-55`、`docs/13:183-187`
7. **"`bookstack`/`keep` 的需求文件能用"** → 它们**不是合法 YAML**（`ScannerError`，一个键比同级兄弟多缩进 4 格），ARC 的裸 `yaml.safe_load`（`core/files.py:14`）直接抛 →
   **主赛道 6 个任务里 2 个拿不到完成记录，而计分要求全部完成** → `docs/06:10-39`、`AGENTS.md`
8. **"需求文本里没有 `Toggle sidebar` / `Note editor`，所以该投图片链路"** → 三条实测都不支持：`requirements.yaml:565`/`requirements.md:413` **逐字有**（零命中来自**大小写敏感**的 grep）；
   喂 `_extract_from_text()` 得 `('Toggle sidebar','button')`；它其实是**共享前置**（8 个 spec 调用）。**图片链路因此降为独立能力问题** → `docs/13:395-407`
9. **"通过数就代表分数"** → 两轮通过数都是 1，但通过的是**不同的一条**（`REQ-2.7.3` vs `REQ-2.1`）→ **通过数会掩盖"分数搬家"** → `docs/13:499-501`
10. **"扩子集丢分是请求压缩造成的"** → 隔离实验（同一份代码 + 2 条需求）`REQ-2.1` **通过（408ms）** ⇒ **压缩无辜**；
    真凶是**共享外壳单文件被多需求竞争挤掉**（切片 1,416→2,512 字符），且闭环三轮都点名了种子数据却**没修成** → `docs/13:505-513`、`PLAN.md:1531-1534`
11. **"`REQ-4.3.7` 稳定失败 / 理论天花板 134/135"** → R5 跑出 **135/135**；**零稳定失败**（124 条稳定通过 + 11 条翻面）；**"天花板 134"作废**，实测区间 **129–135**，满分**可达但不稳定** → `docs/04:222-232`、`PLAN.md:713-725`
12. **"噪声跨度是 6"** → 那是**混口径**算出来的假象；分组后**静置 0–2（跨度 2）/ 污染 2–6（跨度 4）**。
    → **可辨差异下限 2 条、(保留一个改动的门槛) 4 条**；**含污染轮次的比较一律无效**；自我修正：跨度涨到 ≥3 → 门槛退回 6，≥4 → 退回 8 → `docs/04:197-218`、`PLAN.md:753-768,828-829`
13. **"源码里有字符串 ≈ 判据会过"** → 字符串在源码里 ≠ 文本/可访问名可达；**静态检查抓不住"流程没实现"** → `docs/13:677-678,703`
14. **"`final_l1=False` = 产物没过 L1"** → 那是**注入形态**：注进来的是**正则链源码**，L1 的字面子串检查**永远追不到**，而判据的正则会命中 → `docs/13:848-857,954-956`
15. **"L1 仍 ❌ ⇒ 该动 plumbing"** → 触发条件写的是"修复轮两轮都没把整串落进**同一个元素**"，而实测修复轮**落进了同一个元素**（只是用 `/` 连接的自然写法）→ `docs/13:853-857`
16. **"静态判据 `check_db_tables` 不可靠，撤掉"** → **撤错了**：那版检查是对的（能区分三组），错的是后来把它收紧成"必须建在 `init_db.js`"而**误报了 M3b-0**。
    → **留会误报的检查比没有更糟** → `docs/13:217-218,235-239`
17. **"长 sleep 轮询是 MSYS fork 崩的触发条件"** → 第三次（`ctrip1`）**没有长 sleep** 却仍死 ⇒ 触发条件是**长判分轮本身的进程 churn**，不是"我们怎么等" → `docs/13:785-794`
18. **"平台里有网络 / 我们能在平台上自愈"** → 平台侧 agent **没有网络**（装不了依赖、起不来服务，`docs/02:29`）→ **平台侧闭环只能是静态的**；
    判据与 spec 必须**在本地跑**（零 LLM token）。⚠️ 但这条本身只有间接证据（`PLAN.md:1884-1889`），**别当已证事实** → `AGENTS.md` 规则 18
19. **"ARC 保底包能上榜"** → `arc compile` **从未跑过**；且 ARC 在这两个坏 YAML 上硬失败 → ARC **已退出保底退路**，降级为防线 → `PLAN.md:2082-2089`、`STATUS.md:309-313`
20. **"需求里没有可访问名契约"** → `accessible name` 一词在六个赛题 **0 命中**，但句式**各不相同**（12306 引号式；prestashop/stackoverflow/ctrip/keep 散文式零引号）
    → **不能为单一句式写死抽取器**；12306 引号式召回 98% 只对 12306 成立 → `docs/06:107-129`、`PLAN.md:254-278`

---

## 4. 可复用的零件清单（文件 → 做什么 → 值不值得搬）

**判据：纯代码零 LLM + 已被验证 → 值得搬。**

| 路径 | 它做什么 | 出处/验证 | 搬? |
|---|---|---|---|
| `pipeline/reqcompile/yamlrepair.py` | **解析器驱动的缩进修复**（失败取错误行号 → 从附近真实缩进值挑候选 → 只接受让错误位置严格后退或让文件通过的改动 → 否则带诊断放弃，绝不产出半修好的树） | 实测 bookstack 修 2 处（20→16）、keep 修 1 处（12→8），都能解析（节点 56/45）；修复写进 `ValidationReport.repairs` `docs/06:41-51` | ✅ **强推**（2/6 赛题的生死线，`PLAN.md:2204`） |
| `pipeline/reqcompile/loader.py` | 加载 + 校验，**补上 ARC 全缺的四项**：重复 id、未知 dependency、依赖环、children 环（ARC 的加载器只做 safe_load + 取 id，`core/files.py:11-23`） | `loader.py` 模块头；7 个目标全绿 `eval/check_reqcompile.py` | ✅ |
| `pipeline/reqcompile/scenarios.py` | 场景索引（测试标题靶子）：无 id 的场景按 `{req_id}#{order}` 合成稳定 id | 标题契约 1:1 验证：135/135 完全一致 `docs/06:61-104` | ✅ |
| `pipeline/reqcompile/a11y.py` | 模式表驱动可访问名抽取（四条规则）；`\btabs?\b` 这类**词形后缀坑**已在 `docs/06:156-163,225-230` 记死 | 12306 引号式覆盖 **98%**；六 app 引号式召回 35–93% `docs/06:184-191` | ✅ **但必须带局限**：入口是 `_iter_quoted()`，**散文名词在结构上抽不到**（`docs/06:199-202`） |
| `pipeline/reqcompile/prose.py` | 第 5 条规则：散文名词枚举 + role 映射（**位置约束**：只抽祈使宾语位/角色名词位） | 落地后空转率**全降无一变差**：12306 2→1%、bookstack 19→0%、ctrip/keep/prestashop/stackoverflow →8%；精度 55→70%、召回 82–85% `docs/13:383-386` | ✅ |
| `pipeline/generate/scaffold.py` | 骨架落地 + `KNOWN_FIXES`（**上游模板 bug：`init_db.js` 的 `initPromise` IIFE 没有 `return`** → 第二次 `initializeDatabase()` 返回 undefined → 之后任何查询助手都 TypeError）；修复**打在产物上**（平台用自己的模板） | `scaffold.py:64-101`；上游 bug 见 `PLAN.md:1036-1050` | ✅ **强推** |
| `pipeline/generate/schema.py` | **建表注入**：模型只产出纯 SQL 的 `backend/src/database/schema.sql`，管线把它注入 `PRAGMA` 之后、`return database;` 之前（幂等标记块） | gate 0 的**真实卡点**：模型习惯"追加到块末尾"→ `CREATE TABLE` 落到 `return` 后面 = 永不执行的死代码 → 表现为"完全没建表" `schema.py` 模块头 | ✅ **强推** |
| `pipeline/verify/jsscan.py` | JS/TSX **词法级**括号/引号配平扫描 | 以 `node --check` 当仲裁：110 个后端 `.js` → **TP=1 FP=0 TN=109 FN=0**；官方模板 + 6 份历史产物 ≈108 文件 → 0 问题 `docs/13:320,341-342` | ✅（**必须先在语料上量精度**） |
| `pipeline/verify/hittable.py` | **可命中性**：正则体靶子用"能否被某个 locator 命中"判，而不是源码子串存在（L1 与 `predict_judge` **共用一处实现**） | 位置集合**实测钉死**（`docs/13:911-919`）：文本节点（命名族✅/字段族❌）、`aria-label` ✅✅、`title` ✅✅、`placeholder` ❌✅、**`name` 属性 ❌❌（不再是位置）** | ✅ |
| `pipeline/verify/l1.py` | **L1 静态闸门**（13 项，`l1.py` 里 `check_*`）：import 解析 / 路由与入口可达 / 可访问名存在 / 数据库脚手架未改 / 脚本完整 / ESM-CJS / 词法配平 / ARIA 名来源 / **Express 5 路由** / 种子字面量 | 逐项都有实测教训，见 §5 | ⚠️ **逐项搬、但每项都要重新量精度**：其中 `check_db_tables`/`check_js_balance`/`check_seed_literals` 都出过假阳性 |
| `pipeline/verify/loop.py` | 闭环编排：L1 → 模型自检 → 定向修复 → L1 复验，**有界轮次**（默认 3 → **实际只有 2 次修复机会**） | ROI 已量：闭环 ON 29,213 token / 1 通过 vs OFF 14,316 / 0 → **默认保持开着** `PLAN.md:1826-1831` | ⚠️ **要重设计**（旧形态占单轮 50–65% token，且实测把对的代码改坏） |
| `pipeline/provenance.py` | 指纹盖在**产物出生处**（`.arc/provenance.json`），含 `pipeline_dirty`、`tokens_generation_only` / `tokens_total` 两个字段分开命名 | 记录里 `tokens_total` 曾记成"闭环之前"的数（15,833 = 全程 42%）`docs/13:639-640`；**hash 会被重排换掉 → 必须同时带 `git_subject`** `AGENTS.md` | ✅（字段设计照抄） |
| `pipeline/arc_runtime/` | 从 ARC 整包复制的 SDK（events / traceability / runtime） | 平台读到了我们的 traceability：`109 requirements and 86 scenarios` `docs/09:156-162` | ✅ **必须搬**（红线 8） |
| `pipeline/templates/web-react-express/` | 模板副本（**优先用平台自带那份**，包内这份只是兜底） | `docs/09:143-144` | ✅ |
| `eval/bench.sh` | 测量唯一入口：`setup/start/stop/reset/test/status/verdict/hold/recover`；互斥锁 + 心跳（20 秒）+ 四条件过期判定 + 进程**树**杀 + 端口释放验证 | `eval/bench.sh:9-21`；`eval/test_lock.sh` **12 条判据全绿** | ✅（**锁的语义值得整套照搬**） |
| `eval/score_app.sh` | 给一个应用目录打分：**冒烟是第一步** → 清 sqlite → `db:prepare:e2e` → 起服务 → 跑官方 spec → 计 `ERR_CONNECTION_REFUSED` → 出 RunRecord | `eval/score_app.sh:10-14,40-63` | ✅ |
| `eval/extract_run.py` | 从判分日志抽 RunRecord（自带"汇总行缺失 → 打 WARNING"） | 曾静默失败三次，见 §5 | ✅（**带护栏的版本**） |
| `eval/package.sh` + `eval/check_bundle_strings.py` | 打包（在 `pipeline/` 内部执行）+ 合规校验 + 红线 9 机器闸门 | `eval/package.sh:34-84`；实测抓到 4 处 `docs/08:37-38` | ✅ **强推** |
| `eval/subset_closure.py` | 子集三步闭合（数 helper → 取闭包 → **对表**）；「未覆盖」栏 = 必须进硬清单的名字 | 抓到过 `'BookStack'`（本子集文本没写、判据要）`runs/README.md:485-486` | ✅ **零 token** |
| `eval/testspec.py` | ②③ 共用的"靶子闭包发动机"：调用点分类 + 实参抽取 + 动词分类（`expect*`=必须显示 / `fill*`=测试自己输入）+ 覆盖率与**未识别片段** | 覆盖率 bookstack 5%→100%、keep 93%→100%、stackoverflow `0/0`→1044/1044 `docs/13:658-660,697` | ✅ |
| `eval/predict_judge.py` | 判分前逐 spec 预测 + 覆盖率 + `--truth` 混淆矩阵 | 标定：bookstack TP=30/FP=0（后 32/0）、stackoverflow TP=65/FP=0，覆盖率 451/451 `docs/13:675-677,899-901` | ✅ |
| `eval/regress_l1.sh` / `eval/locator_probe.js` / `eval/backfill_per_test.py` | L1 在**历史产物语料**上的回归；locator 位置集合的实测探针；RunRecord 字段回填（带"有汇总却抽不到逐条就拒绝写"的护栏 + 幂等） | `docs/13:586-587,911-919,572-573` | ✅ |

**搬运顺序（按"零 LLM 收益 / 成本"排）**：① `eval/testspec.py` + `subset_closure.py` + `predict_judge.py`（纯静态零 token，能在生成**前**告出该显示哪些名字）→
② `eval/score_app.sh` + `extract_run.py`（本地判分，唯一需要 npm/浏览器的一环）→ ③ `eval/bench.sh` 的**锁/心跳/verdict/hold/recover 五件套**（把"该 app 必须有 `project/`"换成"指向任意应用目录"）→
④ `eval/package.sh` + `check_bundle_strings.py`（提交前闸门）。
**`eval/gate0_run.sh` 不要搬**：硬编码绝对路径（`gate0_run.sh:12,22`）、**进脚本第一件事就无锁强杀 3301**（`:33-36`，与并发纪律直接冲突）、判据①失败只打 ⚠️（`:95`）、结尾永远 `ALL DONE`（`:114`）。
只抄它两条设计：**`--reuse-app` 把"生成"与"判据"解耦**（零 token 复跑判据）、**`cygpath -w` 喂 Windows python**。

**不值得搬**：`pipeline/generate/implement.py` 的**硬编码 CHUNKS 形态**（`PIPELINE_REQ_IDS` 换 app 还是产出注册应用 → **必然 0/32**，`docs/13:299`）、
`implement.py:491` 的 design 调用（**无降级路径**，见 §5.1）、`llm.py:132` 的固定 `MAX_REQUEST_CHARS=24000`（**当成硬异常抛**的用法）。

---

## 5. 已知的坑（会毁实验或丢分的操作）

**5.1 🔴 最致命：design 那一次调用没有降级路径，而全量需求下它的请求体必然超预算**
- `pipeline/generate/llm.py:132`：`MAX_REQUEST_CHARS = 24000`（`PIPELINE_LLM_MAX_REQUEST_CHARS` 可调）；`llm.py:151-156`：**超限直接 `raise RequestTooLarge`**。
- `pipeline/generate/implement.py:491`：`design = chat(cfg, _design_prompt(requirement_brief), stage="design")`
  —— **这一行外面没有 `try/except`**（`implement.py:486-494`），异常一路冒到 `pipeline/main.py:421-423` → `mark_run_failed` → **整个 run 死**。
- 实测（本次会话新算，零 token，命令见下）：**六个 app 的全量 brief 在 design 这一发上全部超 24000**：

| app | 全量 brief 字符 | design 请求字符 | 超预算 |
|---|---:|---:|---:|
| keep | 19,909 | **24,412** | +412 |
| bookstack | 22,759 | **27,262** | +3,262 |
| prestashop | 47,223 | **51,726** | +27,726 |
| stackoverflow | 51,823 | **56,326** | +32,326 |
| ctrip | 76,385 | **80,888** | +56,888 |
| 12306 | 116,427 | **120,930** | +96,930 |

  `STACK_CONTRACT` 单独 **3,729 字符**（`implement.py:36-103`）；`design_req = len(system)+len(user)`，即 `STACK_CONTRACT + brief + 输出 JSON 模板`。
  复算（`python -B`，不写 `__pycache__`）：
  ```bash
  python -B -c "import sys; sys.path.insert(0,'pipeline'); \
  from reqcompile import load_requirement_tree, extract_accessible_names; \
  from generate.implement import build_requirement_brief, _design_prompt; \
  t,_=load_requirement_tree('repos/arc-bench/arc-bench/webapp/<app>/requirements/requirements.yaml'); \
  b=build_requirement_brief(t, extract_accessible_names(t), None); \
  print(len(b), sum(len(m['content']) for m in _design_prompt(b)))"
  ```
  → **这解释了 2026-09-26 那次平台 FAILED**（旧方案只在**子集**下跑通过，全量从未跑通；见 §6）。
- 同一形态**已经发生过一次并被写进方案**：`PLAN.md:593-599` + `PLAN.md:1910-1917` —— `RequestTooLarge: 19,267 字符 ≈ 80%`，
  生成**中止在 10/13 块**，缺外壳与首页块、**闭环完全没跑**，`EXIT=1` → **烧掉的 token 换不回可判分的产物**；
  原文预测：**"修复前的包在平台上跑全量 app 也会中途 `Run failed` → 0 分"**（`PLAN.md:1915`、`docs/13:455`）。**这句话后来应验了。**
- 修法（旧方案最后采用的形态，可当设计参考）：**在拼请求时就按弹性优先级压体积**，而不是"超线就抛"——
  `REQUEST_SAFE_CHARS = MAX_REQUEST_CHARS × 0.7`（`implement.py:112`），四级收紧档（`implement.py:360-394`）；
  13 块最大请求 **19,267（80%）→ 10,818（45%）**（`docs/13:452-456`）。
- **给新管线的硬要求**：① **任何"付费长流程"的硬失败都必须有"就地降级"路径**，异常只留给真正不可恢复的情况（`PLAN.md:580-599`，已升格为原则）；
  ② **预算检查要在拼请求之前做**，不许靠 `except` 兜底；③ **全量需求从第一天就是默认输入**，子集只能是调试开关。

**5.2 并发跑 bench = 两份结果都不可信**
- 事故实录：两个会话 21 秒内先后 `bench.sh test`，第二个的 `reset` 按"谁占端口"换掉了第一个的后端，
  而 **playwright runner 不占端口**所以没被杀 → A 脚下的数据库被重置却继续跑完 → **两份日志都像正常结果，两份都不可信**，
  已标记 `runs/*.INVALID-concurrent.*` → `docs/04:64-80`、`AGENTS.md`。
- 纪律：**动测试之前先 `./eval/bench.sh status`**；有锁或端口被占 → **不要跑任何 bench 子命令（包括 `reset`/`stop`）**；`bench.sh test` 是**全局独占资源**（`AGENTS.md` 硬规则）。
- ⚠️ `status` 的结论行随版本变过：**"端口被占"不再阻断子命令**（会给"🟢 可运行 / 上一轮留下的后端无害"）——**读结论行，不要照旧结论行动**（`AGENTS.md` 多会话并发节）。
- ⚠️ 锁的**过期判定是四条件**（PID 已死 **且** 无 runner **且** 端口空闲 **且** 锁年龄 > 2h）；**"是否在跑"看心跳（20 秒 touch）不看 PID**（`AGENTS.md`、`eval/bench.sh:239`）。
- ⚠️ **锁的持有者与"干活的那一方"可以不同时死** → `status` 不自动清理、`recover` 要人工确认（`docs/13:60-62,787`）。

**5.3 DB 状态累积**
- 实测：同一份冻结产物，**不重置库**时 120 通过 / **15 失败**；`db:prepare:e2e` 后 → **132 / 0**。
  原因：`db:prepare:e2e` 只在第一次准备数据库，之后命中**同一个 db 文件**，累积账号/订单污染后续用例 → `docs/04:8-29`。
- → **每次测量前必须重置数据库并重启后端**；`score_app.sh` 现在跑前清 sqlite（`score_app.sh:100-105`）。
- → **每轮之间用 `--clean` 重建工作区，不要用 `--resume`**（resume 复用队列和已有代码，不是独立重复）→ `docs/04:49`。
- → **固定 `ARC_TEST_DATE`**（测试是日期相关的，`tests/helpers.ts` 第 3 行读它）；`score_app.sh:30` 默认 `2026-09-20`；`extract_run.py` 曾把它写死成 `None` 而**没人填** → 记录无法自证（`docs/13:457-458`）。

**5.4 长等待 → MSYS fork 崩（`0xC000026B` / errno 11）三次同形**
- 第 1 次 `r4-keep4` 跑到 **17/32**；第 2 次 `ctrip1` 跑到 **71/125**（`docs/13:613-617,785-788`）。
- 🔴 **代价是误归因**：死的是**等待它的那个进程**，而日志看起来像"判分失败" → 会有人去修一个没坏的东西。
- 处置：① **用后台任务启动判分**，不要在**同一条命令**里 `sleep` 轮询（**启动即返回，过一阵另起一次调用查看**）；
  ② 中断的日志当场改名 **`*.INVALID-interrupted.*`**，**不进任何通过数**（`bench.sh status` 的未完成迹象扫描会跳过它）；
  ③ 恢复 = `./eval/bench.sh recover`（预检）→ `--force` → 重跑 → `AGENTS.md` 硬规则 19、`docs/13:615`。

**5.5 日志阅读顺序（别把"我们自己的中止规则"读成平台问题）**
- `docs/08:99-106` 四问顺序：① 生成跑完没（`generation-agent exit code: 0`）→ ② 后端起来没（`Template application is reachable on http://127.0.0.1:3000`）→ ③ 测试跑起来没（`Test progress 0/86`）→ ④ 结果（`Playwright results parsed: passed=…, failed=…, score=…`）。
- 先看 run 页的 **Stage 1/2/3 时间线**，再看底部 Stdout（`docs/09:216-222`）。
- ⚠️ 结束语 `Runner exited with test failures or runtime errors` **即使每一步都成功、0/86 也照出** → `docs/09:93,126`。
- **`runs/` 里的 LogRecord 只有带 `N passed` 汇总行才可信**；没有汇总行的是中断产物，`extract_run.py` 会自动打 `WARNING`（`AGENTS.md`）。

**5.6 "判据没报错 ≠ 判据跑过了"（静默失败已同型 3 次）**
- `gate0_run.sh` 的内联 Python 拿到 Git Bash 路径（`/c/Users/…`）→ `ModuleNotFoundError` → **判据① 在三次调用里全失败**，而三次都"看起来正常" → `docs/13:322-325`。
- `per_test_failed` 一直是空的（`RE_FAILED` 要求行首就是 `[browser]`，而失败行带 `x` 前缀）；`RE_TEST_LINE` 又要求 `[browser]` 标记，而 quickstart 那 8 份日志**没有** → 那些记录**一条逐条都抽不到** → `docs/13:523-526,574-575`。
- **守恒自检是恒真式**（`passed+failed+skipped+flaky == total` 永远成立）→ 改用日志自报的 `Running (\d+) tests` 当对照量 → `docs/13:558-559`。
- → **每条判据都要留一个能失败的东西**（变异测试 / 护栏 / 回归列），否则它证明不了任何事。

**5.7 假阳性检查比没有检查更糟**
- `check_db_tables` 类型不匹配（`used` 是 `(表名, 文件)` 元组集合、`created` 只有表名 → `used - created` 永远不消掉任何元素）→ 每个产物报同样 4 条；
  另需**匹配前先剥 JS 注释**（`seed_db.js` 的 JSDoc 里有 `FROM tests` 被当 SQL 读）→ `docs/13:248-254`。
- `check_js_balance` 指向装过依赖的目录时**扫 7616 文件、报 322 条误报**；正则字符类里的 `'` 与反引号被读成跨行字符串 → `docs/13:341-342`、`AGENTS.md` 规则 16。
- `classify_helper` 把 `clickNamed`/`openBooks`/`fillField` 全判 `input` → bookstack **32/34 条失败的首阻一条都没进硬清单** → `docs/13:889-891`。
- → **新检查接进闸门之前，先在历史产物语料上跑**（`%TEMP%` 下已有 6+ 份），看它是"又宽又松"还是"又紧又假"。

**5.8 闸门规则本身会变成恒真式 / 会自锁**
- "降级后硬靶子数不变"这条判据在散文式 app 上**恒真（0 = 0）** → 判据改成"降级后 `(role, name)` 清单条数不变" → `PLAN.md:1443-1454`。
- `SLICE_MAX_CHARS = 6000`（`chunkplan.py:45`）在大 app 上**会频繁触发降级**，而旧降级分支会**把可访问名整块丢掉**（实测两块：13,306→2,400、10,144→1,916，硬/软靶子 0/60 与 0/41）→ `PLAN.md:1435-1453`。
- 🔴 **旧方案自己列过一条"必须排在所有全量生成之前"的零 token 前置，而它从未做完**：
  `PLAN.md:1936-1945` —— 修 `chunkplan.py:342-346` 的降级分支（先渲染去重 `(role, name)` 清单、再用剩余预算放场景描述），
  并注明"**现在跑全量 = 读一个靶子被整块丢掉的产物的分，钱白花**"。**新管线要么先修这个，要么不要有会丢靶子的降级分支。**
- 规则自锁的先例：`main` 分支的合并判据若写"审核没有任何问题"，而审核**几乎总会带 🟡** → **main 会被永久挡住** → 所以约定必须显式打标 `## 🔴 必须先改`（`AGENTS.md` 提交纪律）。

**5.9 其他会毁实验的操作**
- **跑测量前 `git status` 必须为空**，否则 RunRecord 会落成 `dirty=True` / `commit=None`（实测最近四份里三份是）→ `AGENTS.md`。
- **子集模式的预算/读数不能当全量形态**："12306 预算 1,200,000"是**全量 brief** 的值；子集模式预算 **562,832**（brief 仅 354 字符）——两个数都按设计（`docs/13:818`）。
- **记录里的 `git_commit` 全部不可达**（两次历史重排换掉了所有 hash：可达 0 / 不可达 21 / 无指纹 15）→ **新记录必须同时带 `git_subject`**（`AGENTS.md`）。
- **`.gitattributes` 强制脚本 LF**：`eval/bench.sh` 的 shebang 一旦带 `\r` 会报 `bad interpreter`，看起来像"脚本坏了"（`AGENTS.md`）。
- **带管道调用 `bench.sh start` 可能"看起来卡住"**：Windows 下 detached 进程仍继承管道句柄——**它不是故障，别强杀**（`AGENTS.md`、`STATUS.md:511-514`）。
- **判分每轮会把后端留在 3301**（实测 PID 挂过约 17 小时）——**这是常态收尾动作，不是故障**（`docs/13:413,442,467,498`）。
- **跑判据前必须先确认探活 200**：一次"0/6"其实是后端依赖没装（`GET /` → 000），被当成配置结论（`docs/13:193-195`）。
- **上位机路径给内联 Python 时要 `cygpath -w`**（`docs/13:322-325`）。
- 🔴 **`--clean` / `--resume` 只存在于 ARC**（`repos/agentic-requirement-compiler/src/main.py:128`）；自研 `pipeline/main.py` 只有 `compile` / `doctor`。
  → "每轮用 `--clean` 重建工作区"这条纪律在自研管线上**无对应开关**，只能自己删输出目录（旧做法 `eval/gate0_run.sh:74` 的 `rm -rf "$DIR/app"`）。**这是最容易白跑一轮的地方。**
- **端口有两个旋钮**：`eval/bench.sh:26` 读 `ARC_RUNTIME_PORT`，`eval/score_app.sh:28` 读 `PORT`。只设一个 → 后端起在 3302、判分打 3301（打到上一轮的残留后端）。
- **判分脚本的静默降级清单**（搬工具时逐条补"确实执行了"的证据）：`gate0_run.sh:95` 判据①失败只打 ⚠️ 且**结尾永远 `ALL DONE`**（`:114`）；
  `subset_closure.py:91-93` 载入需求树失败会**静默退化**成"全量需求文本"口径（覆盖率被高估）；`testspec.py:60-63` 覆盖率 `0/0` **伪装成"干净"**。
- **跨包耦合点（重写后不改就静默失效）**：`pipeline/verify/hittable.py:60,77` 与 `pipeline/reqcompile/loader.py:371` 被 `eval/predict_judge.py:59-60` / `eval/subset_closure.py:88-93` 直接 import；
  `eval/package.sh:96-97` 硬编码了 `reqcompile/ design/ generate/ verify/ report/` 五个目录名（换名字 → 报 5 条假失败）；
  `eval/check_bundle_strings.py:32-36` 的 app 名正则是题目特定的；`eval/extract_run.py:92` 会把 `REQ-1.2.spec.ts` 切成 `REQ-1.2.`（**带尾点**，下游按 req_id join 全失配）。
- **清理只到一层**：`score_app.sh:102-108` 只删 `backend/` 的 `*.db`（maxdepth 1）→ **子目录里的 sqlite 不会被清**，DB 累积噪声从那儿进来。

---

## 6. 可信的读数基线（**子集 vs 全量必须分清——这一节最重要**）

### 6.1 先把口径钉死
- **判分侧一直是"该 app 的全量套件"**：keep 32 / bookstack 34 / stackoverflow 66 / prestashop 86 / ctrip 125 / 12306 135 条，分母就是全量（`runs/20260923T105954Z-12306-score-12306-1.log: 1 passed / 134 failed (total 135)`；`runs/20260922T104719Z-keep-score-r4-keep4-n2.log: 2 passed / 30 failed (total 32)`）。
- **生成侧从来都是"需求子集"**：逐份 RunRecord 实测（本次会话新算）——
  **从 `bookstack1`（`runs/20260922T120254Z-bookstack-score-bookstack1.json`）起的每一份记录，`req_ids` 都非空**：
  `bookstack {REQ-1.1,REQ-2.1}`、`stackoverflow {REQ-1.1}`、`prestashop {REQ-2.1}`、`ctrip {REQ-2.2}`、`12306 {REQ-1.2}`、
  `bookstack-deep1 {REQ-1.2,REQ-2.2,REQ-4.1,REQ-5.1}`；更早的 keep 各轮同样是子集（`runs/README.md:196-208,503-507`）。
  **`req_ids=None` 且 `gen_mode=1`（= 全量需求真生成）的记录：0 份。**
- 🔴 **结论：全量需求的生成路径从未跑通一次。** 所谓"6/6"是 **6 个 app 各自在小子集上生成、然后接受全量判分、各自拿到 ≥1 条通过**。
  再叠加 §5.1 的实测（全量 brief 的 design 请求 6/6 超预算）→ **平台上必然 0 分**，与 2026-09-26 的 FAILED 一致。

### 6.2 六个 app 的读数（全部：子集生成 + 全量判分 + 零人工）
| app | 生成用子集 | 判分 | 通过的是哪条 | 生成 token | 判分墙钟 | 出处 |
|---|---|---|---:|---:|---:|---|
| keep | `{REQ-1.1,REQ-2.1,REQ-2.2,REQ-6.2}` | **2 / 32** (6.3%) | `REQ-2.1` + `REQ-2.7.3`（**n=2 逐条相同**） | 37,290 | 29.0 / 28.9 分 | `docs/13:601-632` |
| bookstack | `{REQ-1.1,REQ-2.1}` | **2 / 34** (5.9%) | `REQ-1.1` + `REQ-2.1`（**都在子集里**） | 18,004 | 34.7 分 | `docs/13:666-673` |
| bookstack（深一刀） | `{REQ-1.2,REQ-2.2,REQ-4.1,REQ-5.1}` | **2 / 34** | **与上一行逐条相同**（预注册第 ④ 格） | 89,568 | 35 分 | `docs/13:935-966` |
| stackoverflow | `{REQ-1.1}` | **1 / 66** (1.5%) | `REQ-1.3` —— **子集外偶发** | 13,830 | 1.0 小时 | `docs/13:700-703,727-729` |
| prestashop | `{REQ-2.1}` | **1 / 86** (1.2%) | `REQ-2.2.1`（5.0–5.1s）—— **子集外** | 22,356 | 1.4–1.5 小时 | `docs/13:733-745,780` |
| ctrip（第 1 轮） | `{REQ-2.2}` | **0 / 125** | 无 —— 第一个没非零的 app | 26,302 | 1.4 小时 | `docs/13:796-798` |
| ctrip（第 2 轮） | `{REQ-2.2}` + 注入 7 行 | **2 / 125** (1.6%) | `REQ-2.2`（**子集那条**）+ `REQ-2.3.1` | 33,069 | 1.5 小时 | `docs/13:945-953` |
| 12306 | `{REQ-1.2}` | **1 / 135** (0.7%) | `REQ-1.2`（**子集那条**，522ms） | 9,663 | 2.4 小时 | `docs/13:813-818,873-880` |

**怎么读这张表（三条限定词，缺一条就会读错）**：
1. **"非零"≠ 有能力**：6 个 app 里有 **4 个**过的不是子集那条（stackoverflow / prestashop）或只有 1 条（全部）。`runs/README.md:456`：`1/66 = 1.5%`，"只说明这个 app 有分，**不说明能力**"。
2. **条数 ≤ 2 时不能记账**：`docs/04` 门槛 = 静置下**可辨差异 2 条 / 保留门槛 4 条**；keep 从 1→2 的那一轮（`r4-keep4`）**明确"不建在 1→2 上"**，靠**成员翻面** + 机制链；bookstack 深一刀同样"条数 2 < 4 → 本轮不记账"（`docs/13:608-609,966`）。
3. **n=2 只覆盖判分噪声，不含生成方差**：keep / stackoverflow / prestashop 各有一次"同产物再判一次、失败集合逐条相同"（`runs/README.md:426,453`）。

### 6.3 有 oracle 的那一条（唯一可信的"上界"读数）
- **12306 参考实现（官方 project/）= 135/135 全量**，**3.0 分钟**，`pass_rate=1.0`，零人工 —— 五轮里一次满分，**实测区间 129–135**（`docs/04:400-431`、`PLAN.md:713-725`）。
- **其余五个 app 没有参考实现**（`STATUS.md:133`）→ 本地只能拿"外部测试"当判据，**没有上界**。
- 12306 套件规模权威值：**135 条测试 / 117 个 spec 文件**（`playwright --list`，`STATUS.md:442`）。
- **六 app 合计 478 条**（平台标注 484，差 +6 = 12306 占 +3）→ `docs/06:88-101`、`PLAN.md:2205`。

### 6.4 quickstart（单需求样例，非主赛道计分，仅 6 条判据）
| 配置 | 通过 | token | 口径 | 出处 |
|---|---:|---:|---|---|
| `deepseek-chat`（官方档） | **6/6** | 92,158 | **零人工** ← 唯一一个零人工满分 | `docs/11:60`、`docs/13:205` |
| `deepseek-v4-flash`（**平台注入的 MODEL**） | **3/6** | 156,343 | 零人工；reasoning 占输出 86% | `docs/12:155-177` |
| `deepseek-v4-flash` + `reasoning_effort=low` | 5/6 | 81,862 | 零人工 | `docs/12:48-55` |
| `glm-5.3-flash` | **0/6** | 53,723 | 5/6 是 `ERR_CONNECTION_REFUSED`（后端崩） | `docs/12:52-55`、`docs/13:211-214` |
| `deepseek-v4-flash` 原始 → 手工修 3 处接线 | 3/6 → **6/6** | 89,948 | **非零人工**（M3a） | `docs/13:138-140` |
| `gate0c` 零人工 5/6 → **手工补一个 `aria-label`** → 6/6 | 6/6 | — | **非零人工** | `docs/13:283-284,317` |

⚠️ **`6/6` 这两个读数都必须带限定词**：一个是"官方档 + 单需求 + 零人工"，一个是"手工修过"。
**零人工 + 平台模型 + 主赛道规模 = 从未达成过。**

### 6.5 不能当基线的东西
- `runs/*.INVALID-concurrent.*`（并发污染，2 份）与 `runs/*.INVALID-interrupted.*`（fork 中断，3 次）→ **不进任何通过数**。
- `e1-keep4`（`tests_total=0`，冒烟失败按纪律不跑判据）、`run d`/`run e`（冒烟挂）→ **不是 0 分，是"没有读数"**。
- 单次对照（如"c/d/e 三次合计 748,244 token、零人工满分 0 次"）是**单次对照，不是统计结论**（`docs/13:326`）。

---

## 7. 给新管线的十条设计约束（每条都由上面某条教训逼出来）

1. **全量需求是默认输入，子集只是调试开关**；第一个闸门 = "全量需求 + 零人工 + 跑完 + 非零"（§6.1、§5.1）。
2. **请求预算在拼请求之前解决，不靠异常兜底**；任何付费长流程都必须有"就地降级"路径（`PLAN.md:580-599`）。
3. **第一道闸是"输出根有 `frontend/` + `backend/`"**，且"跑得完"优先于"跑得好"（`docs/09:29-43`）。
4. **0 通过 = 不上榜**：宁可 6 个 app 都 40%，不要 4 个 90% + 2 个不存在（`PLAN.md:605-609`）。
5. **判据/闭环/自检都只做"能在本地零 token 验的"**（`AGENTS.md` 规则 14/18）；平台侧只能静态。
6. **能用确定性注入的，不要指望模型自觉**：建表注入成功，种子数据没有注入路径 → 产物 `seed_db.js` 与模板占位**逐字节相同**（`docs/13:973-978`）。
7. **可访问名 / 可见文本必须逐字兑现，不许翻译**；中英不对应就是 0 分（`implement.py:58-63`）。
8. **每个 app 的第一刀先用零 token 工具定位**（子集闭合 + 判分前预测 + L1），**预测通过数 = 0 的产物不许送判分**（`PLAN.md:1628-1639`）。
9. **一条判据要么能失败，要么不算判据**；新检查先在历史语料上量精度（§5.6、§5.7）。
10. **先提交再跑测量**（`git status` 为空）、**长判分用后台任务**、**中断产物改名并作废**（§5.4、§5.9）。
