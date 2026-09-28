# 阶段变更（2026-09-27）：旧管线封存，重写为 g2

## 一句话
旧管线（本目录根下的 `pipeline/` / `eval/` / `PLAN.md` 那套，9/20–9/26）**已封存到 `legacy/`**；
新的开发在 **`g2/`** 里从零重写。**要接着做，请读 `g2/README.md`**，不要照 `PLAN.md` 的排期往下走。

## 为什么重写（不是审美问题，是没分）
1. **平台提交从未拿到分**：2026-09-26 那次提交（`arc-bench-lite` 的 bookstack + keep 两个任务）
   两个 run 都是 `status=FAILED`、`score=0.0`、`token_count=0`。
   报错原文：`Command '[python3, /workspace/submission/main.py, …]' returned non-zero exit status 1.`
2. **根因是旧管线自己的请求预算闸门**：`pipeline/generate/llm.py:132` 的 `MAX_REQUEST_CHARS=24000`
   在 `design` 那一次调用上被触发（`pipeline/generate/implement.py:491` 把整份需求 brief 塞进一次请求），
   抛 `RequestTooLarge` → 整个 run 死在**第一次 LLM 调用**上，一行代码都没生成。
   零 token 复算（见 `.rebuild/LESSONS.md` §5.1）：keep 24,412 / bookstack 27,262 / htc 更大 —— **六个 app 全超预算**。
3. **"6/6 非零"是子集读数**：逐份核对 `runs/*.json`，`req_ids=None ∧ gen_mode=1` 的记录 **0 份** ——
   全量需求的生成路径**一次都没跑通过**。

## 新阶段的关键事实（都是这次实测的）
| 项 | 事实 | 出处 |
|---|---|---|
| 官方赛 | 平台 `hackathon`（type=official）：**2 个任务**、200 条测试、`initial_budget_cny=500`、`starts_at=2026-09-01`、**`ends_at=2026-10-17`** | `https://arc-bench.com/api/competitions/hackathon` |
| 官方两个任务 | `hackathon--github`（TASK-011，47 模块/100 测试）、`hackathon--sheet`（TASK-012，24 模块/100 测试） | 同上 |
| ⚠️ 官方测试**不公开** | `/requirements/hackathon--github/tests?catalog=competition` → **404**（而 Lite 的同类接口 200 有文件） | 实测 |
| 本地可判的对应任务 | `arc-bench-lite--bookstack`（34 测试）、`arc-bench-lite--keep`（32 测试）—— 测试本地有，已拷进 `g2/packs/*/tests/` | `repos/arc-bench/arc-bench/webapp/<app>/tests` |
| 9/26 提交的实际去向 | `competition_id=arc-bench-lite`，两个 run 分别是 `arc-bench-lite--bookstack` / `--keep` | `/api/runs?limit=20` |
| 凭据 | **平台确实注入了模型凭据**（9/26 日志里 `key: set(len=46)`、`model: deepseek-v4-flash`）→ 旧 A1 问题已答 | `docs/13` 归档 + 本次日志 |
| 榜单规模 | 公开榜 API 现在有 **213 行**（9/20 快照是 15 行） | `/api/competitions/leaderboard?limit=100` |

## 旧资产怎么用
- `legacy/`：旧工作区的完整拷贝（含 `pipeline/`、`eval/`、`docs/`、`runs/`、`PLAN.md`、`STATUS.md`、`reviews/`）。**只读参考**。
- `.rebuild/LESSONS.md`：**从这里开始读**。旧管线的平台硬事实、红线、被证伪的假设、可复用零件、已知坑、可信读数基线、十条设计约束。
- `repos/`：上游仓库（未动）。

## 红线照旧有效（重写不改这些）
1. 提交包根目录**必须直接是 `main.py`**，不能套目录；
2. **根层不能有 `package.json` / `index.js` / `index.ts`**；
3. `.env` 绝不提交；API key 不写进任何会被提交或被引用的文档；
4. **不得预埋答案**：模板零业务语义，"全仓"读作"提交包"；
5. 不修改 `repos/`；
6. 平台调用契约：`python3 <submission>/main.py <需求目录> --output-dir <输出目录>`。