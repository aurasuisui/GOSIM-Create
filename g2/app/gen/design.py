"""结构设计：分块问模型"这个应用需要哪些路由 / 端点 / 表"，再把答案**确定性合并**。

为什么要分块 + 合并（而不是一次问全量）：
  旧管线 `design` 那一次请求把整份 brief 塞进去，实测 6/6 个 app 都超预算（最小 24.4k 字符），
  而那一行没有降级路径 → 平台 run 死在第一次调用 → 0 分（`.rebuild/LESSONS.md` §5.1）。

合并规则（全部确定性，零 LLM）：
  routes   : 按 path 去重（先出现的赢）
  endpoints: 按 (method, path) 去重
  db_tables: 按表名去重；同名的 columns 取**并集**（合并不丢列）
  auth / error_shape: 取第一个非空
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .llm import DESIGN_OUTPUT_TOKENS, LLMConfig, Ledger, chat, fit, message_chars
from ..reqcomp.planner import Group
from ..reqcomp.spec import render_block

STACK_CONTRACT = """### 技术栈（固定，不要改）
- 前端：React + TypeScript + Vite，组件放 `frontend/src/pages/`，API 封装放 `frontend/src/api/`。
- 后端：Express（CommonJS！用 `require`/`module.exports`，**不要写 import/export**）。
- 数据库：模板自带的 sqlite 脚手架 —— 从 `./database` 引 `run/get/all/exec/withTransaction`，
  **建表只写在 `backend/src/database/schema.sql`**（纯 SQL），不要自建连接。
- 路由挂 `/api/...`；前端用相对路径 `/api/...` 调后端（同端口托管）。
- 实体值（名字、标题等）必须渲染在**它自己的元素**里，不要拼进句子。
- 导航/入口用真正的 `<a>` / `<Link>`（判据按 role=link 找），不要用 button 冒充。
"""

DESIGN_SYSTEM = """你是资深全栈工程师。为下面的需求做**极简结构设计**：只输出 JSON，不要解释文字。

只输出这一段 JSON（键名照抄）：
{
  "routes": [{"path": "/shelves", "component": "ShelvesPage", "purpose": "一句"}],
  "api_endpoints": [{"method": "GET", "path": "/api/shelves", "request": "字段名", "responses": ["200 + 形状"]}],
  "db_tables": [{"name": "shelves", "columns": ["id", "name", "..."], "unique": ["name"]}],
  "auth": "一句话：怎么登录、怎么在刷新后保持；不需要就写 \"无\"",
  "error_shape": "错误响应体的 JSON 形状"
}

要求：
- 路由必须覆盖需求里出现的每一个页面（判据会直接点链接进去）。
- 端点要够把需求里的场景做完（成功 / 校验失败 / 冲突）。
- **一切按需求来**：需求里没有的东西不要设计。宁少勿多。
"""


@dataclass
class Blueprint:
    routes: list[dict] = field(default_factory=list)
    api_endpoints: list[dict] = field(default_factory=list)
    db_tables: list[dict] = field(default_factory=list)
    auth: str = ""
    error_shape: str = ""
    per_group: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"routes": self.routes, "api_endpoints": self.api_endpoints,
                "db_tables": self.db_tables, "auth": self.auth,
                "error_shape": self.error_shape, "notes": self.notes}


def _extract_json(text: str) -> dict:
    """从模型输出里抠出第一个 JSON 对象（容忍 ```json 围栏与前后废话）。"""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    else:
        start = t.find("{")
        end = t.rfind("}")
        if start >= 0 and end > start:
            t = t[start:end + 1]
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        # 常见小毛病：尾逗号
        cleaned = re.sub(r",\s*([}\]])", r"\1", t)
        return json.loads(cleaned)


def merge_blueprints(parts: list[dict]) -> Blueprint:
    bp = Blueprint()
    seen_routes: set[str] = set()
    seen_eps: set[tuple[str, str]] = set()
    tables: dict[str, dict] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        for r in part.get("routes") or []:
            if not isinstance(r, dict):
                continue
            path = str(r.get("path") or "").strip()
            if not path or path in seen_routes:
                continue
            seen_routes.add(path)
            bp.routes.append({"path": path,
                              "component": str(r.get("component") or "").strip(),
                              "purpose": str(r.get("purpose") or "").strip()[:120]})
        for e in part.get("api_endpoints") or []:
            if not isinstance(e, dict):
                continue
            key = (str(e.get("method") or "GET").upper(), str(e.get("path") or "").strip())
            if not key[1] or key in seen_eps:
                continue
            seen_eps.add(key)
            bp.api_endpoints.append({"method": key[0], "path": key[1],
                                     "request": str(e.get("request") or "")[:200],
                                     "responses": [str(x)[:160] for x in (e.get("responses") or [])][:4]})
        for t in part.get("db_tables") or []:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            cols = [str(c).strip() for c in (t.get("columns") or []) if str(c).strip()]
            if name in tables:
                existing = tables[name]["columns"]
                for c in cols:
                    if c not in existing:
                        existing.append(c)
                for u in (t.get("unique") or []):
                    if u and u not in tables[name]["unique"]:
                        tables[name]["unique"].append(u)
            else:
                tables[name] = {"name": name, "columns": cols,
                                "unique": [str(u) for u in (t.get("unique") or []) if u]}
        bp.auth = bp.auth or str(part.get("auth") or "").strip()
        bp.error_shape = bp.error_shape or str(part.get("error_shape") or "").strip()
    bp.db_tables = list(tables.values())
    return bp


def design_pack(tree, groups: list[Group], cfg: LLMConfig, *, ledger: Ledger | None = None,
                out_dir: Path | None = None, log=print) -> Blueprint:
    """逐块问结构，再合并。每块请求都先过 `fit()`。"""
    parts: list[dict] = []
    for i, g in enumerate(groups, 1):
        block = render_block(g.specs, header=f"## 需求块 {i}/{len(groups)}：{g.label}")
        messages = [
            {"role": "system", "content": DESIGN_SYSTEM},
            {"role": "user", "content": f"{STACK_CONTRACT}\n\n{block}"},
        ]
        messages = fit(messages, priorities=[0, 1], log=log)
        log(f"  [design {i}/{len(groups)}] {g.key} {len(g.specs)} 条需求 "
            f"/ 请求 {message_chars(messages)} 字符")
        text = chat(cfg, messages, stage=f"design-{i}", ledger=ledger, log=log,
                    max_tokens=DESIGN_OUTPUT_TOKENS)
        if out_dir is not None:
            (Path(out_dir) / ".arc").mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / ".arc" / f"design-{i}.json").write_text(text, encoding="utf-8")
        try:
            parts.append(_extract_json(text))
        except Exception as exc:  # noqa: BLE001
            log(f"    ⚠️ 第 {i} 块输出不是 JSON（{exc}）→ 跳过这一块的设计")
            continue
    bp = merge_blueprints(parts)
    if not bp.routes:
        bp.notes.append("没有任何路由产出 —— 设计阶段可能全部失败")
    return bp