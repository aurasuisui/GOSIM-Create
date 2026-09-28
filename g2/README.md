# g2 —— 重写版薄管线

> 2026-09-27 起，旧管线封存到 `../legacy/`，新开发都在这里。
> 先读 `../.rebuild/LESSONS.md`（旧管线的平台硬事实 / 红线 / 被证伪的假设 / 已知坑），再读本文件。

## 目标与赛题

- **官方赛**：平台 `hackathon`（official，`2026-09-01 → 2026-10-17`，`initial_budget_cny=500`），两个任务：
  `hackathon--github`（TASK-011，47 模块 / 100 测试）与 `hackathon--sheet`（TASK-012，24 模块 / 100 测试）。
- ⚠️ **官方这两个任务的测试不公开**（`/requirements/<id>/tests?catalog=competition` → 404），**本地无法判分**。
- ✅ **本地可判分的对应任务**：`arc-bench-lite--bookstack`（34 条）与 `arc-bench-lite--keep`（32 条）——
  测试在 `repos/arc-bench/arc-bench/webapp/<app>/tests/`，已拷进 `g2/packs/<app>/tests/`（只读参考）。
- **开发顺序**：先在可判分的这两个任务上把管线做对（有 oracle、能迭代），再上官方任务。

## 目录

```
g2/
  main.py                 入口（平台契约：main.py <需求目录> --output-dir <目录>）
  app/reqcomp/            需求编译（零 LLM）：解析容错 / 靶子抽取 / 规格渲染 / 分组
  app/gen/                生成：scaffold 骨架 · design 结构设计 · writer 写文件 · llm 客户端
  app/verify/staticcheck.py  零 token 静态判据
  packs/<app>/            赛题包（requirements.yaml + .md + tests/，从平台与基准仓库取）
  templates/web-react-express/  随包模板（已修 init_db 的上游 bug）
  tools/                  本地工具：score.py 判分 · pack_report.py 静态报表 · tasks.py 任务表
  work/                   本地产物与实验工作区（不入库）
  runs/                   判分结果与日志（不入库）
```

## 怎么跑

```powershell
# 0. 先看需求包长什么样（零 token）
powershell -NoProfile -File g2/tools/py.ps1 g2/tools/pack_report.py

# 1. 只做需求编译 + 分组（零 token，不调模型）
powershell -NoProfile -File g2/tools/py.ps1 g2/main.py compile g2/packs/bookstack --output-dir g2/work/bookstack

# 2. 到"结构设计 + 文件计划"（3 次便宜调用，约 33k token / 4 分钟）
powershell -NoProfile -File g2/tools/py.ps1 g2/main.py plan  g2/packs/bookstack --output-dir g2/work/bookstack

# 3. 全流程生成
powershell -NoProfile -File g2/tools/py.ps1 g2/main.py emit  g2/packs/bookstack --output-dir g2/work/bookstack

# 4. 本地判分（起服务 + 跑官方测试，34 条约 35 分钟；后台跑）
powershell -NoProfile -File g2/tools/py.ps1 g2/tools/score.py bookstack g2/work/bookstack --label try1
```

## 与旧管线不同的四条硬规则（都由旧管线的失败逼出来）

1. **请求预算先于发送**：任何付费请求都必须先过 `llm.fit()`（就地降级），
   `chat()` 只接受装得下的消息。旧管线是"超预算就抛异常"，而那一行没有 try/except →
   **整个 run 死在第一次调用**（2026-09-26 平台 FAILED 的机制）。
2. **一文件一调用**：请求小、输出小（旧管线实测"合并输出越大越容易被网关断连"）。
3. **能用确定性代码判的，绝不问模型**：建表 SQL、靶子清单、分组、静态检查全部零 token。
4. **读数必须带口径**：是全量还是子集、是本地判分还是平台判分，写清楚（旧管线"6/6"全是子集读数）。

## 当前状态

见 `../STATUS.md`（每次会话结束更新）与 `../PHASE-NOTE-g2.md`（阶段变更说明）。