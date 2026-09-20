# 2026 OAIC 智能体软件工厂国际黑客松和大奖赛系列

> 信息来源：官网 https://create.gosim.org/factory26/（含 #awards 奖项区块）及站内规则页、研习营页，外加评测平台 https://arc-bench.com/。
> 首次抓取：2026-09-07（UTC）。**最近复核：2026-09-19**。
> 抓取方式：Chromium 实际渲染后取正文。队伍数在接口未就绪时会显示 0，属加载状态差异。
> 本地原始文本见 `raw/`，截图见 `screenshots/`。

---

## 0. 2026-09-19 复核：发生了什么变化

9/7 存档后 12 天内，官网有三处实质性变更，另有若干信息首次落地。按重要程度排列：

| # | 变化 | 影响章节 |
|---|---|---|
| 1 | **研习营已办完，且与 9/7 公布的计划完全不同**（9/10–12 三天十讲，非 9/7–9/20 六讲+三答疑）；三课录像、课程仓库、参考实现仓库、ARC-Bench 注册步骤、TRAE/魔搭资源全部上线 | §8 |
| 2 | **规则页「提交物」从三件套简化为一条**：只需在 arc-bench.com/competition 提交智能体。此前"源码 + 生产轨迹 + Demo"的写法已删除 | §6 |
| 3 | **ARC-Bench 平台已开放**，含 6 个赛道；主赛道 = 6 个真实 Web 应用、468 需求模块、484 条测试 | §9、§11 |
| 4 | 官网首页排行榜区块首次出现真实数据（Smoke Competition 快照，15 条） | §3 |
| 5 | 队伍数 344 → **360（顶部通栏）/ 365（名册）**；顶部通栏文案改为"研习营已开营" | §1、§7 |
| 6 | 报名已截止；名册区块文案改为"报名已截止（09/07 23:59），已报名队伍仍可登录修改资料" | §7 |

**仍未公布**：三项评分指标的权重（官网两处均写"权重将在确认后公布"）；ARC-Bench 榜单的 Efficiency 计算口径（需登录）。

**官网自身尚未同步的地方**（如你负责更新官网，这几处值得改）：首页赛程区块与规则页 §2 仍写"研习营 9/7–9/20 · 六讲课程 + 三场答疑"，与研习营页实际的"9/10–12 · 十讲"不符；首页 hero 与页脚仍挂"报名截止：09/07 23:59"作为行动号召。

---

## 1. 基本信息

- 中文名：2026 OAIC 智能体软件工厂国际黑客松和大奖赛系列
- 英文名：2026 OAIC International Hackathon for Agentic Factory and Grand Challenge Series
- 主题：GOSIM 黑客松 · 软件工程 × 智能体 / Agentic Software Factory
- 主办：开放智能体产业联盟（OAIC）
- 认证：启悟社区（教育部支持）/ Qiwoo Community
- 形式：全球开放，不设资历门槛，个人与团队均可参加；初赛与决赛均为线上，无需到场
- 核心命题：当代码由 agent 大规模生成，交付如何重新变得可信、可复现、可追溯
- 比赛任务：在统一模型与沙箱中，用智能体实现真实软件功能，比拼正确率、Token 效率与完成速度
- 报名状态（9/19）：**已截止**，截止时间 2026-09-07 23:59（北京时间）。已报名队伍仍可登录修改资料
- 队伍数（9/19）：顶部通栏 360 支；队伍名册页脚"查看全部 365 支队伍"

## 2. 赛程（四段，9/7–10/17）

| 阶段 | 时间 | 形式 | 内容 |
|---|---|---|---|
| 研习营 Bootcamp | **9/10–9/12**（官网赛程区块仍写 9/7–9/20） | 线上（腾讯会议，每日一个会议号，均有录像） | 十讲课程，主题改为"测试驱动与需求编译"路线 |
| 初赛 Qualifier（唯一入口） | 9/21–9/30 | 线上 | 在 ARC-Bench 主赛道提交智能体 |
| 决赛 Grand Challenge | 10/1–10/7 | 线上 | 更具挑战、基于真实复杂企业需求的命题；初赛排行榜 Top 20 晋级 |
| 颁奖 Awards | 10/17 | 深圳 GOSIM 大会闭幕式现场 | 荣耀颁奖；特等奖队伍受邀参会 |

- 研习营页顶部注明"智能体软件工厂黑客松将于 9 月 21 日举行"

## 3. 奖项与排行榜

**奖项（官网 #awards 区块，第 04 节）**

- 总奖金池：24,000 美元，共 20 个现金奖项
- 特等奖 Grand Prize：2 个，每队 3,000 美元（2×3000=6000）
- 一等奖 First Prize：4 个，每队 2,000 美元（4×2000=8000）
- 二等奖 Second Prize：6 个，每队 1,000 美元（6×1000=6000）
- 三等奖 Third Prize：8 个，每队 500 美元（8×500=4000）
- 合计：6000+8000+6000+4000=24,000，吻合
- 其他权益：所有完赛队伍获完赛证书并在官网留档；赛程期间可用赞助方提供的开源模型 Token 额度；特等奖队伍受邀参加 GOSIM 深圳 2026 大会
- 注意：部分第三方转载文章写的是 5,500 美元 / 6 奖项旧方案，以官网当前 24,000 美元 / 20 奖项为准

**首页实时排行榜（第 07 节，9/19 首次有数据）**

- 数据源为 Smoke Competition，页面标注"公开赛榜单，非正式初赛排名"
- 快照时间戳：09/11 12:25:10，共 15 条。榜首"睿欣达工场"100.0% 通过率 / 0.06M Token / 2m 3s
- 全部条目模型列均为 `deepseek-v4-flash`
- 该区块自称"排行榜将在 9 月 21 日初赛开赛后实时更新"

## 4. 评测标准（3 指标 + 4 底线）

三项指标均由机器自动采集，**权重仍未公布**（官网 9/19 原文："权重将在确认后公布"）：

1. GUI 测试用例通过率（行不行）：验收用例通过数 / 总数，来自规格系统的测试与沙箱环境
2. Token 效率（省不省）：该队所有模型调用 Token 之和，由模型网关计量
3. 完成时间（快不快）：开工到提交的墙钟耗时，由计时器与 git 时间戳采集

四条评审底线（F01–F04）：

- F01 公平：环境、Token 配额、规格与测试对所有队一视同仁，规格与测试独立于参赛队
- F02 可复现：轨迹、日志、种子全落盘，同输入可重跑出同分
- F03 环外不变量：队伍能改自己的 Harness，改不到测试、网关计量与评分
- F04 可申诉可审计：每个分背后都有测试日志或 trace 依据可查

另：ARC-Bench 榜单表头用的是另一套字段——Rank / User / Model / Avg. Pass Rate / Feature Coverage / Efficiency / Total Token / Runtime，与官网三指标不完全同名。9/19 实测榜单已可见（无需登录），**Efficiency 单位是 `pass/CNY`**，即每元人民币换来的通过率，榜单上同时出现 `deepseek-v4-flash` 与 `glm-5.3-flash` 两个模型。这与规则页写的"Token 效率"（Token 总量）不是同一个量纲，以平台为准。

## 5. 模型与 Harness

- 统一开源模型 Token（组织方发放，经统一网关限额与计量）：Kimi、GLM、MiniMax、DeepSeek
- Harness 不限：Octos、HAgency、ARC、Claude Code、自研及其他 Agents 均可参加
- 首页排行榜实际出现的模型为 `deepseek-v4-flash`

## 6. 提交物（9/19 已简化，注意口径变化）

- **规则页当前写法（第 4 节原文）**：在比赛平台（https://arc-bench.com/competition）提交您的智能体软件工厂软件。
- **首页 FAQ 09 写法**：只需向黑客松比赛平台提交智能体，其他材料均不需要。
- 两处现已一致。9/7 存档中规则页的"① 可运行的复刻 ② 完整生产轨迹 ③ 3–5 分钟 Demo"三件套**已从官网删除**，不要再引用。
- ARC-Bench 主赛道的 HOW TO COMPETE 四步：上传 agent 快照或选内置 → 把大需求编译成可运行小模块 → 依次跑 GitHub 风格与 spreadsheet 风格任务 → 查看证据并迭代到输出稳定

**上传约定（ARC-Bench API Doc，9/19 抓取）**

交的是"一个能跑的 agent 包"，入口文件名固定：

| 语言 | 入口文件 | 依赖声明 |
|---|---|---|
| Python | `main.py` | `requirements.txt` |
| JavaScript | `index.js` | `package.json` |
| TypeScript | `index.ts` | `package.json` |

存在 `package.json` 时 runner 会先装 Node 依赖再调用 agent。

**运行期必须留下的痕迹（不是额外手工交的材料）**

agent 需调用内置 `arcbench_agent_runtime` SDK（Python 包），由 SDK 负责写：

- `.arc/runner-events.jsonl` — 固定事件协议，追加写
- `.arc/traceability/*.json` — 键值 JSON 表

后端文件监听器读取这两处，据此刷新前端的需求树、可追溯面板、提交历史与预览。官方明确要求：不要手写事件 payload，只调 SDK 高层方法。

这三样（agent 包 + 事件流 + 可追溯表）共同构成规则里 F04"可申诉、可审计"的依据，但只有 agent 包是你主动上传的。

## 7. 报名

1. 由队长或主要联系人提交一个队伍账号（提交即完成报名）
2. 之后可用报名邮箱和密码登录官网、查看或修改资料
3. 9/19 状态：报名通道已关闭

**ARC-Bench 注册（研习营页第 4 节，9/19 新公布）**

1. 进入 arc-bench.com 首页
2. 点击右上角 Register
3. 选择 Hackathon account
4. 使用黑客松官网报名时填写的邮箱注册并登录
5. 登录后点击右上角用户名缩写图标进入个人空间
6. 在左侧查看并复制 API key

个人空间里的 API key 同时用于登录 meter.arc-bench.com 查询用量。

注意：首页第 07 节仍保留旧文案"ARC-Bench 的开放与登录方式会另行通知""收到主办方通知前，无需在 ARC-Bench 进行额外操作"，与研习营页已公布注册步骤相矛盾，以研习营页为准。

## 8. 研习营（9/19 已办完，信息全部更新）

**实际安排**

- 日期：2026 年 9 月 10 – 12 日，每天下午 13:30 – 16:30（北京时间）
- 形式：腾讯会议，每天一个会议号（9/10：160444306 / 216857；9/11：198246412 / 517088；9/12：710916730 / 246412）
- 定位：把参赛者放到同一条技术与资源起跑线上；推出了本次比赛技术路线之一"测试驱动与需求编译"

**十讲课程主题**（与 9/7 存档的"六讲"完全不同）

| # | 主题 | 日期 |
|---|---|---|
| 01 | 基于智能体的测试驱动开发 | 9/10 |
| 02 | 模型选择与预算分析 | 9/10 |
| 03 | 可视化的需求编译 | 9/10 |
| 04 | 需求编译中的人机交互实践 | 9/11 |
| 05 | 版本管理与智能体调试 | 9/11 |
| 06 | 需求的增量迭代与演化 | 9/11 |
| 07 | 实战参考实现（以 Octos 为例） | 9/12 |
| 08 | 实战参考实现（ARC Agent） | 9/12 |
| 09 | 实战参考实现（HAgency） | 时间待定 |
| 10 | 互动答疑（Office Hour） | 时间待定 |

**课程资料索引**

- 第一课录像：https://www.bilibili.com/video/BV1a5Yu68EcS/
- 第二课录像：https://www.bilibili.com/video/BV1h3YE6WEyz/ （含 9 段分章：开场、Lab 02+ 测试驱动开发 GUI、魔搭社区介绍、Lab 05 版本回溯与视图切换、Lab 06 增量编译实践、Lab 07 交互式编译与迭代、模拟赛题集 arc-bench 介绍、agent 上传提交说明、Octos 参考实现）
- 第三课录像：https://www.bilibili.com/video/BV1uHYR6pEaU/
- 第一课课程材料与 Lab：https://github.com/code-philia/agentic-software-engineering-hackathon
- octos-arc 代码仓库：https://github.com/octos-org/octos-arc （Octos 智能体的 ARC 适配版）
- API 用量查询：https://meter.arc-bench.com/user
- 课程交流讨论区：https://bbs.qiwoo.edu.cn/t/topic/15
- 赛事交流讨论区：https://bbs.qiwoo.edu.cn/t/topic/16

**更多资源（合作方额度，可选、不计入评分）**

- TRAE · 字节跳动：IDE，学生证可再送 200 元 token 费用；活动截至 2026-12-31
- 魔搭社区 · 魔粒：每位学员一次性 300 魔粒；获奖的 20 个作品若部署到魔搭创空间，额外奖励 1000 魔粒。需自行申请加入组织 https://modelscope.cn/organization/Agent-Software-Factory-Hackathon ，社区周日/周一批量审批

## 9. 平台与链接

- 官网：https://create.gosim.org/factory26/
- 奖项锚点：https://create.gosim.org/factory26/#awards
- 规则：https://create.gosim.org/factory26/rules （9/19 实测可直接访问）
- 研习营：https://create.gosim.org/factory26/bootcamp
- 评测平台：https://arc-bench.com/ ；注册 /register；登录 /login；榜单 /competition；练习场 /playground；研究 /research；API 文档 /api-doc
- 用量查询：https://meter.arc-bench.com/user
- 课程/参考实现仓库：https://github.com/code-philia/agentic-software-engineering-hackathon 、https://github.com/octos-org/octos-arc
- 早期参考实现（9/7 存档，官网已不再列出）：octos-org/arc-adapter、onewesong/hafleet-arc
- 合作伙伴：OAIC（https://visionforum.ai/#about）、启悟学习社区（https://qiwoo.edu.cn/）、亚信科技、TaoToken.net、TRAE、华为云、全法中国青年科创协会

## 10. FAQ（官网 9 问原文要点）

01 谁可参加：全球开放，无门槛，线上即可。02 报名完成标志：提交队伍账号即创建成功。03 是否必须初赛：是，初赛是唯一入口；研习营可选。04 赛题：初赛复刻 GitHub/Spreadsheets（Actions、组织权限、审计、Rulesets），决赛为真实企业命题，细节见 arc-bench.com。05 模型：必须用组织方发放的 Kimi/GLM/MiniMax/DeepSeek Token。06 Harness：不限。07 评分：三指标机器自动采集。08 奖项：见第 3 节。09 提交：见第 6 节。

注：FAQ 04 的赛题描述（GitHub/Spreadsheets 复刻）与 ARC-Bench 主赛道实际列出的 6 个 Web 应用任务（火车票、BookStack、携程、Keep、PrestaShop、Stack Overflow）不一致，前者更像分类说法，以后者为准。

## 11. ARC-Bench 赛道（9/19 新开放）

平台首页分 Playground / Competition / Research / API Doc 四区。Competition 下 6 个赛道均为 OPEN：

| 赛道 | 起止 | 任务数 | 测试数 |
|---|---|---|---|
| ARC-Bench-Lite | 2026-09-10 – 2036-09-10 | 2 | 66 |
| **ARC-Bench: Web Application**（Featured，主赛道） | 2026-09-10 – 2036-09-10 | 6 | 484 |
| Smoke Competition | 2026-09-01 – 2026-10-17 | 2 | 2 |
| Smoke Competition (Evolution) | 2026-09-01 – 2026-10-17 | 2 | 4 |
| Ticket Booking | 2026-09-01 – 2026-10-17 | 1 | 10 |
| Ticket Booking (Evolution) | 2026-09-01 – 2026-10-17 | 0 | 0 |

**主赛道 ARC-Bench: Web Application 的 6 个任务**

| 任务 | 需求模块 | 可执行测试 |
|---|---|---|
| Train Ticket Booking System | 117 | 138 |
| BookStack Knowledge Base System | 34 | 34 |
| Ctrip Travel System（本需求集聚焦用户认证与账号管理） | 133 | 126 |
| Keep | 32 | 32 |
| PrestaShop E-commerce Website | 86 | 87 |
| Stack Overflow Platform | 66 | 67 |
| **合计** | **468** | **484** |

- 计分规则：同一份提交必须完成全部 6 个任务，才计入聚合榜（"A complete leaderboard score is calculated only when one submission has a completed run for every task in this competition"）
- 四步流程：上传 agent 快照 → 编译需求为可运行模块 → 依次跑 GitHub 风格与 spreadsheet 风格任务 → 查看证据并迭代
- Competition Notes：以需求文档为准；每次改动足够小以便逐步验证；用任务产物解释 agent 改了什么
- 榜单未登录时持续 Loading，Current leader 显示 "No data yet"

## 12. 本地文件说明

- `raw/主页_中文.txt`：首页全文（赛程、奖项、评分、FAQ、队伍名册、Smoke 榜单）
- `raw/规则_中文.txt`：规则页 8 节全文
- `raw/研习营_中文.txt`：研习营全文（课程表、十讲、资料索引、注册步骤、更多资源）
- `raw/ARC-Bench_平台_中文.txt`：平台首页、赛道列表、主赛道详情（9/19 新增）
- `raw/主页_英文.txt`、`raw/规则_英文.txt`、`raw/研习营_英文.txt`：**仍为 9/7 旧版，未复核**（英文站切换按钮在无头渲染下点不动，未能重新抓取）
- `raw/链接_主页.txt`、`raw/链接_研习营.txt`：站内外链接清单（9/7 版）
- `screenshots/首页顶部截图.png`：首页渲染截图（9/7 版）
