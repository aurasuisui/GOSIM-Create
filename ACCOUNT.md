# 参赛账号信息

> 记录时间：2026-09-20。API key 不写在本文件里，见工作区根目录 `.env`。

## 身份

| 项目 | 值 |
|---|---|
| 队伍名称 | suisui的队伍 |
| 报名邮箱 | 2436448088@qq.com |
| GitHub 账号 | aurasuisui |

## 平台

| 平台 | 地址 | 用途 |
|---|---|---|
| 黑客松官网 | https://create.gosim.org/factory26/ | 报名、队伍资料、赛程 |
| ARC-Bench | https://arc-bench.com/ | 提交 agent、跑任务、看榜单 |
| 用量查询 | https://meter.arc-bench.com/user | 按 API key 查人民币消耗 |
| 模型网关 | https://api.arc-bench.com/v1 | 本地实验与比赛的统一接口 |

## 凭据位置

- **API key**：工作区根目录 `.env`（`ARC_API_KEY`）
- 需要时按 `.env` 里的「块 A」复制到 `repos/agentic-requirement-compiler/.env`，
  按「块 B」复制到 `repos/agentic-software-engineering-hackathon/Lab/Lab02/.env`
- 三个目标文件都已被各自的 `.gitignore` 覆盖，工作区根目录也加了 `.gitignore`

## 待确认

- [ ] ARC-Bench 平台账号是否已注册（研习营页第 4 节：进首页 → 右上角 Register → 选 Hackathon account → **用报名邮箱 2436448088@qq.com 注册**）
- [ ] API key 是否有效（首次启用时先用 `npm run doctor` 或一次最小调用验证）
- [ ] 报名是否确实完成（官网报名已于 09/07 23:59 截止）
- [ ] 视觉模型（`VISUAL_MODEL`）用哪个 —— 参考实现的 main model / visual model 分工商未定

## 安全提醒

API key 已出现在会话记录中。该 key 直接关联比赛 token 额度（按人民币计费），
建议**比赛结束后立即在平台个人空间重新生成**，作废这一个。
