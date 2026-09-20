# pipeline/ —— 提交物

这里是**要交付的代码**。整个目录的内容最终会被打成 zip 上传到 ARC-Bench。

> 与 `repos/` 的区别：`repos/` 是只读的上游参考，**任何改动都发生在本目录**。

---

## 目录职责

| 目录 | 职责 | 对应管线阶段 |
|---|---|---|
| `arc_runtime/` | 从 ARC 复制的 `arcbench_agent_runtime`（MIT）。事件流与 traceability 的平台约定格式 | 贯穿 |
| `reqcompile/` | 需求解析 + 契约抽取：场景索引、可访问名契约、夹具规格、依赖图校验 | ① 需求编译 |
| `design/` | 按页面/路由聚类，每簇一次调用产出设计 JSON | ② 批量设计 |
| `generate/` | 骨架落地（确定性写文件）+ 实现生成 | ③④ |
| `verify/` | 三层验证闸门（可执行性 / 空实现反证 / 参考实现对拍） | ⑤ |
| `report/` | traceability 落盘、事件上报、token 计量 | 贯穿 |
| `templates/` | 从 `repos/arc-template/templates/web-react-express/` 复制。**只放基础设施，零业务语义** | ③ |

根目录放 `main.py` 与 `requirements.txt`（入口契约）。

---

## 入口契约（不能违反）

```bash
python main.py <需求目录|requirements.yaml> -o <输出目录> [--type web]
```

- `main.py` 的**输入输出语义不可改**（赛规）。需要开关就用环境变量，不要加 CLI 参数。
- `requirements.txt` **只增不减**。
- 支持环境变量（平台很可能靠这个传工作区）：`ARCBENCH_OUTPUT_DIR` / `ARCBENCH_PROJECT_DIR` / `ARCBENCH_RUNNER_EVENTS_PATH` / `ARCBENCH_TRACEABILITY_DIR`。

完整的打包与合规清单见 `docs/02-提交契约与打包.md`。

---

## 生成的应用要满足什么

**这是最容易被低估的部分。** 生成的应用必须满足 C1–C10 十条硬契约（见 `docs/02` §2），其中最容易踩的是：

- `frontend/dist/index.html` —— **精确路径**
- `backend/package.json` 必须有 `start` 和 `db:prepare:e2e`
- **单端口**：backend 托管前端产物，前端无独立 dev server
- 端口读环境变量，**不硬编码**
- 后端监听 `0.0.0.0`

---

## 两条设计红线

### 1. 以"外部测试能做什么"为设计中心

外部 Playwright 测试**禁止**用 `page.goto`、`page.request`、`localStorage`、`.evaluate`、`toHaveURL`、class/id 选择器。

→ 它只能靠**点击导航 + 可访问名/role + 可见文本**验收。
→ **考的不是功能是否存在，是应用是否暴露稳定的 accessible name / role 结构。**

所以生成时要：约束 prompt + 静态检查 JSX 的可访问名 + 用探针验证场景入口可达。

### 2. 成本是排序口径，不是附属指标

`pass/CNY` 决定排名。任何设计决策先问省不省 token。具体手段见 `docs/05-成本与模型路由.md`。

---

## 建议的建置顺序

1. **入口与骨架**：`main.py` + `requirements.txt` + `arc_runtime/`（整包复制）+ `templates/`（复制）
2. **需求解析**：`reqcompile/` —— 先把需求树读懂并抽出四样东西（场景索引 / 可访问名 / 夹具 / 依赖校验）
3. **最小闭环**：用一个最简单需求（`repos/agentic-requirement-compiler/example/ticketbooking-quickstart`，单 REQ-1）跑通"生成 → 起服务 → 跑官方测试"，**先要能跑完，再谈跑得好**
4. **加闸门**：`verify/` 三层
5. **加计量**：`report/` 的 token 落盘 —— 没有它后面所有优化都是盲飞
6. **优化**：批量、模型路由、上下文裁剪

**第 3 步之前不要碰任何优化。** 先有尺子，再有改进。
