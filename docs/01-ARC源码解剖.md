# 01 · ARC 源码解剖

> 目的：让我们**不必重读那 12k 行**就能做设计决策，并且知道该从里面搬什么。
> 代码位置：`repos/agentic-requirement-compiler`（HEAD `a119f22`）。**只读参考，不修改。**
> 结构：约 12k 行 Python，52 个文件。

---

## 一、它是什么

不是"一个 agent"，而是一台**批处理编译器**：

```
需求树 YAML
  → 每节点两个队列任务（DESIGN / IMPLEMENT）
  → 严格 DFS 串行执行
  → 每阶段调一次 deepagents agent 会话
  → 结果写进 .arc/ 下的 JSON 存储
  → 每阶段一个 git commit
```

**所有"智能"在三段 prompt + 5 个 SKILL.md 里；所有"确定性"在 `core/workflow.py` + `core/phases.py` 里。**

---

## 二、模块地图

| 目录/文件 | 职责 | 关键符号 |
|---|---|---|
| `src/main.py` | 入口。argparse + 转发到 workflow | `main()` :416，`cmd_compile()` :217 |
| `src/core/workflow.py` | 队列状态机 + 结果汇总 | `ARCWorkflowManager` :76，`_build_processing_tasks` :864，`_next_runnable_task` :970 |
| `src/core/phases.py` | 三阶段实现 + 预算 + 测试结果判定 | `WorkflowPhaseRunner` :21，`TDD_RUN_TESTS_BUDGET` :17，`_prepare_tests` :753 |
| `src/core/service.py` | **进程级单例** runtime | 模块级 `_runtime` :10 |
| `src/core/visual_analysis.py` | 多模态入口，每图一次调用 | `analyze_and_attach_visual_references` :82 |
| `src/agents/interface_designer.py` | DESIGN 阶段 | `InterfaceDesigner.run` :49 |
| `src/agents/test_generator.py` | 测试生成 | `TestGenerator.run` :60 |
| `src/agents/test_driven_developer.py` | 实现阶段（**唯一能改产品代码**） | `TestDrivenDeveloper.run` :51 |
| `src/agents/context/pipeline.py` | 上下文装配 + 5 层缓存 | `build_agent_context` :461，`build_agent_context_split` :574 |
| `src/agents/runtime/factory.py` | 组装 deepagents（权限矩阵 + 中间件） | `build_stage_agent` :80，`create_deep_agent` :117 |
| `src/agents/runtime/stage_discipline.py` | 阶段硬约束 | `StageDisciplineMiddleware` :24，常量 :10-14 |
| `src/agents/runtime/runners.py` | 调用 + 日志 + 流式回退 | `ainvoke_stage_agent` :20，`DEFAULT_RECURSION_LIMIT=5000` :17 |
| `src/app_type_handler/web.py` | **web 主战场**：测试执行、端口治理、E2E 运行时 | `run_test_group` :898，`_start_backend_runtime` :634 |
| `src/app_type_handler/base.py` | 应用类型抽象 + 模板定位 | `initialize_workspace` :51，`copy_template` :78 |
| `src/skills/` | 5 个 SKILL.md（合计仅 141 行） | 见下 |
| `src/arcbench_agent_runtime/` | **可观测/持久化层，整包复用** | `AgentRuntime` :12，`EventClient`，`TraceabilityStore`，`GitClient` |

### 五个 skill

`auth-session-consistency`(23 行) / `leaf-full-design`(27) / `leaf-test-layer-selection`(36) / `non-leaf-ui-only-design`(26) / `tdd-test-failure-repair`(29)

由 `agents/skills/selection.py` 按节点类型**确定性**挑选，模型只能读被选中的那份。

---

## 三、主干调用链

```
main.py:416  main()
  ├─ build_parser() ; argv 首参不是 compile/doctor/config 就自动补 "compile"  (:420)
  └─ asyncio.run(cmd_compile(args))
       ├─ _ensure_dotenv_loaded()                     (:219 → :50)
       ├─ _locate_requirement_file(path)              (:251 → :65)  只接受 yaml/yml 或含 requirements.yaml 的目录
       ├─ ARCWorkflowManager(...)                     core/workflow.py:79
       │    └─ start_compilation()                    :269
       │         ├─ load_requirement_tree()           :300 → :146 → core/files.py:11 (yaml.safe_load)
       │         ├─ initialize_project()              :308 → :175
       │         │    ├─ _has_existing_application()  :176 → :154  ← 决定 scaffold / evolution
       │         │    ├─ configure_runtime(...)       :191 → core/service.py:13
       │         │    ├─ create_app_type_handler(...) :203 → app_type_handler/__init__.py:27
       │         │    │    └─ initialize_workspace(seed_template=)
       │         │    │         → check_prerequisites → copy_template(:78) → install_dependencies
       │         │    └─ git.ensure_repo()            :222
       │         └─ compile_requirement_tree()        :314 → :339
       │              ├─ store_requirement_tree()     :360
       │              ├─ _load_or_create_processing_queue()  :362 → :797
       │              │    └─ _build_processing_tasks()      :864
       │              │         后序：本节点 DESIGN → 递归子节点 → 本节点 IMPLEMENT
       │              ├─ _recover_interrupted_queue() :368 → :922
       │              └─ while True:                  :446    ← 严格串行
       │                   ├─ _next_runnable_task()   :447 → :970  取列表序第一个 PENDING
       │                   ├─ _run_task(task)         :465 → :1113
       │                   │    ├─ DESIGN    → phases.run_design_phase    core/phases.py:55
       │                   │    └─ IMPLEMENT → phases.run_implement_phase :342
       │                   ├─ 成功 → _commit_phase_checkpoint() :486 → :1136（git commit）
       │                   └─ 失败 → _mark_remaining_node_tasks_failed() :495
       └─ _build_compile_result()                     :515 → :1172
```

**退出码语义**（重要）：`main.py:340-345` 有明确注释——只有"调用/配置/运行时失败"才非 0；**单个需求节点失败仍返回 0**。所以平台判定 agent 是否失败主要看产物与事件流，不是退出码。

---

## 四、设计阶段的三个分支

判定在 `core/phases.py:56`：`is_non_leaf = bool(requirement_data.get("children_ids"))`。

| 分支 | 位置 | 行为 |
|---|---|---|
| 非叶子 + 无视觉参考 | `phases.py:81-106` | **完全不调用 agent**，清空设计产物，直接 return True |
| 非叶子 + 有视觉参考 | `phases.py:151-180` | 只跑 InterfaceDesigner；**测试生成显式跳过** |
| 叶子 | `phases.py:193-241` | InterfaceDesigner + TestGenerator 串联 |

非叶子的 IMPLEMENT 也不进 TDD：`phases.py:358-373` 直接标接口 implemented + `result_state=CONVERGED`。

**叶子/非叶子的设计策略判定其实被推给了模型**：`prompts/interface_designer.py:83` 明确写 *The workflow has not pre-classified this node; decide whether it is leaf or non-leaf from `children_ids` and visual references before designing.*

### 设计阶段的硬约束（这部分做得好，值得借鉴）

`agents/runtime/stage_discipline.py:12-13`：
- `_MAX_DESIGN_WRITES = 8` —— 设计阶段最多写 8 个骨架文件
- `_MAX_SKELETON_LINES = 160` —— 单文件最多 160 行
- `:60-61` —— 设计/测试阶段**禁止运行任何 validation 工具**

唯一强校验是接口 `type` 白名单 `UI|API|FUNC|DB`（`phases.py:18`，校验 :709-713），不合法直接 `raise ValueError` → 节点 DESIGN 失败。

---

## 五、TDD 循环

```
run_implement_phase                                    phases.py:342
 ├─ 非叶 → 直接标 implemented + CONVERGED                :358-373
 ├─ tests 为空 → 跳过 TDD 并标完成                        :396-405
 └─ _run_tdd_for_node()                                 :407 → :420
      ├─ 分层固定顺序 Unit → Integration → E2E           TDD_BATCH_ORDER :19
      ├─ 每层 while 直到 exit_code==0 或预算耗尽          :562-619
      │    └─ TestDrivenDeveloper.run()                  :588
      │         每次都是**全新 agent + 全新单条 user 消息**
      └─ set_test_pass_statuses()                        :640
```

**预算**：`TDD_RUN_TESTS_BUDGET = 5`（`phases.py:17`）——含义是**每个测试层独立的 `run_tests` 调用次数**。会话总数上限 = `5 × 层数`（:561），三层时 = 15。

超预算时 `run_tests` 返回伪造的 `Exit Code: 1 ... budget exhausted`（:494-505），**不真的执行测试**。

`run_requested_tests` 是最紧的一段控制逻辑（`phases.py:448`）：限制模型只能跑当前活动层（:467-473）、只能跑本节点已登记的文件（:485-492）。

---

## 六、三个硬伤（本方案要绕开或修掉的东西）

### 硬伤 1：测试生成是整条链上唯一没有闸门的一环 🔴

`_prepare_tests`（`core/phases.py:753-789`）只查五项：`test_id` 非空、`type` 非空、`file_path` 非空、路径正则、同批不重 id。

**没有的校验**（逐项 grep 确认）：
- 文件是否真的存在于磁盘 —— 全段无任何 `exists()` / `is_file()`
- `first_line` 是否与文件首行一致 —— 该字段全仓只有**写入**，没有任何读取比对（死字段）
- manifest 与 `files_written` 是否一致 —— `output_text` 在 `phases.py:194` 被 `tests, _ = ...` 丢弃
- 测试能否被 runner 解析/收集 —— **结构性禁止**：`stage_discipline.py:60-61` 在 test_generation 阶段直接拦截 `run_build`/`run_tests`
- 测试是否在一个正确实现上通过 —— 全仓 grep `reference implementation|golden|oracle|baseline|teacher` **零命中**

**而且方向是相反的**：ARC 显式授权 TDD 去改测试。
`skills/tdd-test-failure-repair/SKILL.md:17` 第 6 条：*If a generated test is invalid, contradictory, brittle, or incompatible with the runner, edit the test while preserving requirement intent*。
`prompts/test_driven_developer.py:132`：*If product behavior is wrong, edit product code. If the test is wrong, edit the test.*

**后果**：一个不可执行/自相矛盾/断言过弱的测试会以"合法产物"身份被冻结，然后 TDD 在预算内既可以让实现去追它，也可以把它改弱——**后者是纯粹白烧 token 且不提升外部通过率**。

**对照**：课程 Lab02 有这道闸门（`Lab/Lab02/src/run/orchestrator.ts:611-676`：先在教师参考实现上跑，不绿就修测试，`maxTestRepairs = 3`，全绿才冻结）。**ARC 没有。**

### 硬伤 2：完全没有 token 计量 🔴

`agents/model/openai_api_adapter.py:111-119` 设了 `disable_streaming: True` 和 **`stream_usage: False`**。非流式响应其实**一定**带 usage，LangChain 会塞进 `AIMessage.usage_metadata`——**ARC 从不读**。

全仓 grep `usage_metadata|total_tokens|cost_|cny` 零命中。`tiktoken` 在 `pyproject.toml` 里声明了但从未使用。

**在按 `pass/CNY` 排序的比赛里，这等于盲飞。**

**现成的插桩点**：`agents/runtime/runners.py:363` 已经在分发 `on_chat_model_end` / `on_llm_end`：

```python
if event_name in {"on_chat_model_end", "on_llm_end"}:
    text = _message_content_text(data.get("output"))
```

`data["output"]` 就是 LangChain 的 `ChatResult`，usage 就在里面。**只差两行**，且天然带 `label`（stage 名）和 `node_id`。

视觉模型是**独立链路**（`core/visual_analysis.py:216` 的裸 `OpenAI(...).chat.completions.create`），不走 adapter，需单独插一针。

### 硬伤 3：预算文案与代码不一致 🟡

- 代码：`TDD_RUN_TESTS_BUDGET = 5`（`core/phases.py:17`）
- prompt：`prompts/test_driven_developer.py:124` 写 *budget of 10 calls*
- skill：`skills/tdd-test-failure-repair/SKILL.md:21` 也写 *budget of 10 calls*

模型按 10 次规划，第 6 次就被拒。纯 bug，改两行。

### 附带发现

- **叶节点撞预算 = 直接判 FAILED**，没有"半通过"状态。`CONVERGED` 只有非叶节点会写（`phases.py:358-373`），`CONVERGED_WITH_FAILED_CHILDREN`（`workflow.py:51`）**全仓只被读、无写入方**，是死分支。
- 层结果落库是**整层 all-or-nothing**：`phases.py:629-640` 把整层的 exit code 写给该层所有 test_id。5 个测试挂 1 个 → 5 个全记 `passed=False`。
- `tdd_handoff.modified_files` 恒为空（`phases.py:676`），配套的 `build_incremental_context`（`pipeline.py:595-620`）**全仓无调用者**，死代码。
- 视觉分析结果写库时被 `str()` 逐项转换（`traceability.py:259,292`），而消费侧用 `isinstance(item, dict)` 过滤（`pipeline.py:145-153`）——**净效果是 `<visual_reference>` 块会渲染成 `[]`，视觉分析文本进不了模型上下文**，只剩"有没有图"这个布尔信号在起作用。

---

## 七、能自由改 vs 不能动

**不能动**（会被二次审查判为越界）：`main.py` 的输入输出契约与退出码语义、`requirements.txt` 的删除。

**可自由发挥**（普通 Python 模块，无签名冻结）：`core/`、`agents/`、`app_type_handler/`、`skills/`、提示词、模型选择、预算策略、traceability 存储格式。

**一处死代码提醒**：`main.py:322` 永远传 `clear_all=False`（`--clean` 在 :255 已 rmtree 过），所以 `cleanup_workspace`（`workflow.py:129`）从 CLI 走是死代码。

---

## 八、该搬走什么

| 内容 | 路径 | 为什么 |
|---|---|---|
| 事件与 traceability SDK | `src/arcbench_agent_runtime/` | 协议是平台约定格式，官方明说不要自己实现；MIT 协议，整包复制 |
| 技术栈契约字符串 | `src/app_type_handler/web.py:1060-1095`（`build_stack_block`） | 原样放进生成 prompt，生成的代码才和测试基建对得上 |
| 五个 SKILL.md | `src/skills/` | 几轮真实跑测攒出来的提示词资产 |
| 设计阶段硬约束的思路 | `stage_discipline.py:12-14` | 骨架写入上限、禁止设计阶段跑测试——这套约束有效，值得照搬 |
| 决赛增量语义 | `workflow.py:154` 的 `_has_existing_application()` + `_plan_requirement_sync()` | 受影响节点 = 新增/变更 + 祖先 + 反向依赖。决赛照这个语义实现 |

### 决赛相关的好消息

`initialize_workspace(seed_template=(mode == SCAFFOLD))`（`workflow.py:210-212`）——**一旦判定为 evolution 模式，模板不会被拷贝**（`base.py:64-68` 只打一行日志）。所以初赛被模板卡住的风险，决赛反而没有。

⚠️ **反向风险**：判据是工作区里除了 `.arc`/`.git`/`requirements` 之外的**任何文件**都算"已有应用"（`workflow.py:35, 154-166`）。**任何多余文件（`.env`、`README.md`）都会把 scaffold 误判成 evolution**，导致模板不被拷贝、前端目录不存在、直接失败。**打包/工作区里千万不要留无关文件。**
