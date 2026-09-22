"""自检 + 定向修复 —— 补 L1 静态查不出的那类缺陷（M3a 的 5.3）。

**分工**：
  - `l1.py` 管**机械**的（import 解析、路由/入口、名字是否出现、脚本完整性）——确定、便宜、零 token
  - 本模块管**语义**的：代码存在且能构建，但**没对准需求**。
    M3a 的三个 5.3 缺陷正是这一类：
      · `App.tsx` 不传 `onRegistered` → 注册成功后不跳转
      · 注册绕过了 auth context → 登录态不更新
      · 首页把用户名包在 `Welcome, …!` 里 → `getByText(username, {exact:true})` 永远匹配不到

**两段式**（先诊断、再修，不要一上来就重写）：
  1. `review()`：把**需求原文 + L1 发现 + 代码全文**交给模型，要它列出**具体违规**
  2. `repair()`：按**文件**分组，只把被点名的文件交给模型改（定向，不重写全仓）

**两条硬约束**（M3b-0 第一轮实测踩出来的）：
  - **不要小截断**：第一版把文件截到 8000 字符，模型于是对被截断的部分**瞎猜**
    （"截断处未确认…可能缺失"），产出噪声违规、白烧 token，还可能改坏代码。
  - **修复 prompt 必须强调相对路径**：第一轮修复把 `RegisterPage` 的 import 改错了层级，
    **引入了新的构建失败**（正是 M3a 5.1 那类）。

**轮次由 `verify/loop.py` 控制**（默认 3），每轮结束都要 L1 复验。
"""
from __future__ import annotations

import json
import re

from generate.llm import MAX_REQUEST_CHARS, LLMConfig, RequestTooLarge, chat
from generate.implement import parse_files, write_files

REVIEW_SYSTEM = ("你是严格的需求验收审查员。只列**能被外部测试抓到的具体缺陷**，"
                 "不要提风格建议，不要对没看到的代码做猜测。只输出 JSON 数组。"
                 "⚠️ 下面「已知契约」里的东西**一律不要报**——它们是刻意的，不是缺陷。\n"
                 "【已知契约】原生 `<select>` 的**初始占位项：文本用需求里的提示语，value 必须是空串**"
                 "（`<option value=\"\">请选择…</option>`）：外部测试直接断言 `toHaveValue('')`，"
                 "把 value 写成提示文本会**直接判失败**。`<meter>` 必须**同时**有 `<label htmlFor>`"
                 "与 `aria-label`（只有 label 时它的 ARIA 名是空的，`getByRole('meter',{name})` 找不到）。"
                 "同理：退出入口是 `role=link`、错误消息在 `role=\"alert\"` 里——这些都不要报。")

# 修复阶段要把同一批契约再讲一遍：**审查的猜测会被当成真缺陷去改代码**。
# 实测代价（2026-09-21 干跑）：一轮 review 把「证件类型初始 value 为空串」报成缺陷 →
# 修复照做、把 value 改成 "请选择证件类型" → `toHaveValue('')` 直接失败（5/6 而不是 6/6）。
REPAIR_SYSTEM = ("你是资深全栈工程师。只修**被指出的缺陷**，不要顺手重构。"
                 "必须输出完整文件，且严格遵守 ===FILE: 格式。"
                 "改到 import 时**必须核对相对层级**（数一下从本文件所在目录到目标文件的目录深度）"
                 "——写错相对路径会让构建直接失败。"
                 "**建表语句只写在 `backend/src/database/schema.sql`（纯 SQL）**："
                 "不要在 JS 里写 CREATE TABLE（管线会把 schema.sql 注入到后端启动流程），"
                 "也不要改 `backend/src/database/` 下的其它文件。"
                 "⚠️ **不许动的契约**：**后端 `.js` 一律 CommonJS**（`require` / `module.exports`），"
                 "写 `import`/`export` 会让后端起不来（脚手架是 module.exports）；"
                 "原生 `<select>` 的初始占位项 value **保持空串**"
                 "（`<option value=\"\">请选择…</option>`，文本可改、value 必须留空）；"
                 "`<meter>` 必须**同时**有 `<label htmlFor>` 与 `aria-label`（缺 `aria-label` 时"
                 "它的 ARIA 名是空的，`getByRole('meter',{name})` 找不到——补上，别删）；"
                 "退出入口保持 `role=link`；错误消息保持渲染在 `role=\"alert\"` 元素里。")

# 允许"被点名但尚不存在"的文件——只有管线指定的建表脚本。
# 其它不存在的路径一律跳过：自检可能把路径写歪（`authRoute.js` vs `auth_routes.js`），
# 照着它新建会多出一个没人 import 的文件。
CREATABLE_IF_ABSENT = {"backend/src/database/schema.sql"}

MAX_CHARS_PER_FILE = 40000

# 提示词模板/清单本身占的字符数（保守估），用来算"这一组能放多少代码"
REVIEW_BODY_OVERHEAD = 2600
# 即使需求 brief 很大也要留给代码的下限——不然一次审查都做不成
MIN_CODE_BUDGET = 3200


def _collect_files(output_dir) -> list[tuple[str, str]]:
    """(相对路径, 全文)。**不做小截断**：截断会让模型对被截断的部分瞎猜。

    跳过 `database/`：那是模板脚手架（含管线注入的建表块），不是模型的产出。
    """
    out: list[tuple[str, str]] = []
    for top in ("frontend/src", "backend/src"):
        base = output_dir / top
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if (f.is_file() and f.suffix in (".ts", ".tsx", ".js")
                    and "database/" not in f.as_posix()):
                out.append((f.relative_to(output_dir).as_posix(),
                            f.read_text(encoding="utf-8", errors="replace")))
    return out


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return (text[:limit]
            + "\n…(此文件过长已截断；**不要对看不到的部分做任何猜测**)")


def group_files(files: list[tuple[str, str]], budget: int) -> list[list[tuple[str, str]]]:
    """按字符预算分组，**不切分单个文件**（切了就等于截断，模型会开始猜）。

    为什么要分组：一次把全仓代码发过去会超 `MAX_REQUEST_CHARS`，而"大请求被网关丢"
    实测已三次——最近一次就是本模块的 review（连续 `RemoteDisconnected` ×2，
    直接把闸门第 0 条的第三次重跑卡死）。分组之后每组都在预算内。
    """
    groups: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    size = 0
    for rel, text in files:
        cost = len(text) + len(rel) + 40
        if cur and size + cost > budget:
            groups.append(cur)
            cur, size = [], 0
        cur.append((rel, text))
        size += cost
    if cur:
        groups.append(cur)
    return groups


def _review_body(requirement_brief: str, l1_text: str, code: str, manifest: list[str],
                 index: int, total: int) -> str:
    part = ("" if total == 1 else
            f"\n⚠️ 这是**第 {index}/{total} 组**：下面是应用代码的**一部分**。"
            "只判断附上的这些文件，**不要**因为某个文件没附上就说它缺失。\n")
    return """下面是**需求**与刚生成的**代码**。请找出代码里**对不上需求**的具体缺陷。

## 需求（验收依据，逐字读；注意关于「页面显示什么 / 怎么进入 / 叫什么名字」的要求）

""" + requirement_brief + """

## 静态检查已发现的问题（可能不完整）

""" + l1_text + """
""" + part + """
## 整个应用的文件清单（**只附上了一部分，未附上的不要下结论**）

""" + "\n".join("  - " + m for m in manifest) + """

## 本次附上的代码

""" + code + """

## 你要找的是这类缺陷（M3a 实测过，都是真的）

1. **入口不可达**：需求要求「从首页点击 X 进入」，但那个链接/按钮没接上回调，或被渲染成不可点的东西。
2. **状态没更新**：注册/登录成功后登录态没有被刷新，首页仍显示未登录。
3. **显示形态不匹配**：需求说「页面显示用户名 / 退出链接」，实际渲染成拼接文本
   （如 `Welcome, {username}!`）或用 `<button>` 而不是链接——外部测试按**精确文本**或
   **role=link** 查找就会失败。
4. **可访问名没兑现**：控件没有用 `<label htmlFor>` 原生关联；下拉不是原生 `<select>`；
   密码强度不是 `role=meter` 或没设 `aria-valuenow`。
5. **错误没渲染成 alert**：校验失败的错误没出现在 `role="alert"` 的元素里（或该元素为空）。
6. **刷新后丢登录态**：只在内存里存状态，没有靠 cookie 重新拉取会话。

## 输出（严格 JSON 数组，不要任何其他文字）

```json
[
  {"file": "frontend/src/App.tsx",
   "problem": "一句话说清哪里对不上需求（要能被验证）",
   "fix": "一句话说明怎么改"}
]
```

没有缺陷就输出 `[]`。

⚠️ **只根据你确实看到的代码下结论**。文件若被截断或未附上，**不要猜「可能缺失」**——
猜测会被当成真缺陷去改代码。
⚠️ **不要提「可以更好」这类建议**，只列会让外部测试失败的东西。"""


def review(output_dir, requirement_brief: str, l1_findings: list[dict],
           cfg: LLMConfig, *, log=print) -> list[dict]:
    """要模型列出"哪些地方对不上需求"。返回 [{"file","problem","fix"}]。

    **按预算分组发**（2026-09-21）：单次请求塞全仓代码会被网关断开，
    见 `group_files()` 的说明。组内预算 = `MAX_REQUEST_CHARS` − 需求 − L1 清单 − 模板开销。
    """
    files = _collect_files(output_dir)
    if not files:
        log("  ⚠️  没有可审查的源码文件，跳过自检")
        return []
    manifest = [rel for rel, _ in files]
    l1_text = "\n".join("  - [" + f["kind"] + "] " + f["file"] + ": " + f["detail"]
                        for f in l1_findings) or "  （无）"
    budget = max(MIN_CODE_BUDGET,
                 MAX_REQUEST_CHARS - len(requirement_brief) - len(l1_text) - REVIEW_BODY_OVERHEAD)
    groups = group_files(files, budget)
    log(f"  自检输入：{len(files)} 个文件 / 代码预算 {budget} 字符 → 分 {len(groups)} 组")

    violations: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for i, group in enumerate(groups, 1):
        code = "\n\n".join(
            "===FILE: " + rel + "===\n```\n" + _truncate(text, budget) + "\n```"
            for rel, text in group)
        msgs = [
            {"role": "system", "content": REVIEW_SYSTEM},
            {"role": "user", "content": _review_body(requirement_brief, l1_text, code,
                                                     manifest, i, len(groups))},
        ]
        try:
            raw = chat(cfg, msgs, stage=f"selfcheck-review-{i}of{len(groups)}", log=log)
        except RequestTooLarge as exc:      # 预算不够也不该让整轮生成炸掉
            log(f"  ⚠️  第 {i}/{len(groups)} 组超预算、跳过自检：{exc}")
            continue
        for v in parse_violations(raw, log=log):
            key = (v["file"], v["problem"][:80])
            if key in seen:
                continue
            seen.add(key)
            violations.append(v)
    return violations


def _is_non_defect(problem: str, fix: str) -> bool:
    """把"其实是符合需求"的条目丢掉。

    为什么必须过滤（干跑实测）：第 2 轮 review 回了 33 条，其中绝大部分的正文写着
    "满足需求 / 无需修改 / 非缺陷"——**它把"合格"也列进了违规**。
    代价不是噪声，是**一整轮全量修复**（4 个文件重写 ≈ 12k token，占那次 run 的 12%）。
    判据要小心：`…不满足需求` 是真缺陷，所以带否定词的条目一律**保留**。
    """
    blob = (problem or "") + " " + (fix or "")
    # ① 出现任何否定/缺陷词 → 一律**保留**（判错的代价不对称：漏掉真缺陷会丢分，
    #    多留一条只是在那一轮多改一个文件）
    if any(w in blob for w in ("不", "未", "没", "缺", "错", "失败", "无法", "不能",
                               "冲突", "绕过", "重复", "多余", "丢", "挂", "覆盖")):
        return False
    # ② 没有否定词、且出现"合格"词 → 判为不是缺陷
    return any(w in blob for w in ("无需修改", "非缺陷", "可以不改", "满足需求",
                                   "已满足", "符合要求", "已正确", "无缺陷"))


def parse_violations(raw: str, *, log=print) -> list[dict]:
    """从模型回复里取 JSON 数组；取不到就当「无发现」（并如实说）。"""
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        log("  ⚠️ 自检没返回 JSON 数组，当作「无发现」")
        return []
    try:
        data = json.loads(m.group(0))
    except Exception as exc:  # noqa: BLE001
        log("  ⚠️ 自检返回的 JSON 解析失败（" + repr(exc) + "），当作「无发现」")
        return []
    out: list[dict] = []
    dropped = 0
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("file") and item.get("problem"):
            problem, fix = str(item["problem"]), str(item.get("fix", ""))
            if _is_non_defect(problem, fix):
                dropped += 1
                continue
            out.append({"file": str(item["file"]).lstrip("./"),
                        "problem": problem, "fix": fix})
    if dropped:
        log("  （过滤掉 " + str(dropped) + " 条「其实是符合需求」的条目——"
            "它们会换来一整轮无效修复）")
    return out


def repair(output_dir, violations: list[dict], cfg: LLMConfig, *, log=print,
           max_files: int = 4) -> list[str]:
    """按文件分组做定向修复；每个被点名的文件单独一次调用。"""
    by_file: dict[str, list[dict]] = {}
    for v in violations:
        by_file.setdefault(v["file"], []).append(v)
    existing_list = sorted(
        f.relative_to(output_dir).as_posix()
        for top in ("frontend/src", "backend/src")
        for f in (output_dir / top).rglob("*")
        if f.is_file() and f.suffix in (".ts", ".tsx", ".js")
    ) if (output_dir / "frontend/src").is_dir() else []

    # 按优先级排序再截断（PLAN §7 的 3a）：priority 0 = 命中契约（回归项），**永不截**
    ranked = sorted(by_file.items(), key=lambda kv: min(v.get("priority", 1) for v in kv[1]))
    keep = [kv for kv in ranked if min(v.get("priority", 1) for v in kv[1]) == 0]
    rest = [kv for kv in ranked if kv not in keep][:max(0, max_files - len(keep))]
    selected = keep + rest
    if len(keep) > max_files:
        log(f"  （契约/回归项 {len(keep)} 个文件**突破 max_files={max_files}**：回归项不许被截）")

    written: list[str] = []
    for rel, vs in selected:
        target = output_dir / rel
        exists = target.is_file()
        if not exists and rel not in CREATABLE_IF_ABSENT:
            log("  ⚠️ 自检点名了不存在的文件 " + rel + "，跳过")
            continue
        text = target.read_text(encoding="utf-8", errors="replace") if exists else ""
        if len(text) > MAX_CHARS_PER_FILE:
            text = text[:MAX_CHARS_PER_FILE] + "\n…(已截断；不要猜看不到的部分)"
        problems = "\n".join(
            "  " + str(i + 1) + ". 问题：" + v["problem"] + "\n     建议改法：" + v["fix"]
            for i, v in enumerate(vs))
        inventory = "\n".join("  - " + n for n in existing_list) or "  （无）"
        body = ("这个文件被验收审查点名了，请**只修这些缺陷**。\n\n"
                "文件：`" + rel + "`"
                + ("" if exists else "（**尚不存在，请创建它**）") + "\n\n"
                "被指出的问题（逐条修掉，不要顺手改别的）：\n" + problems + "\n\n"
                + (("当前内容：\n```\n" + text + "\n```\n\n") if exists else "")
                + "## 项目里**已经存在**的文件（import 只能指向这些，或你本次一并新建的）\n\n"
                + inventory + "\n\n"
                + "## 输出格式（严格遵守）\n\n"
                "**必输出**：\n"
                "===FILE: " + rel + "===\n```\n<修好之后的完整文件>\n```\n\n"
                "**可选**：如果某个 import 指向的文件确实不存在，你可以**一并新建它**"
                "（路径必须在 frontend/ 或 backend/ 下）：\n"
                "===FILE: frontend/src/lib/xxx.ts\n```\n<新文件内容>\n```\n\n"
                "⚠️ 但**不要**为了图省事新建 `lib/`、`utils/` 这类抽象层——"
                "能直接写在被点名的文件里就直接写。\n"
                "只输出这些 ===FILE: 段落，不要任何解释。")
        msgs = [
            {"role": "system", "content": REPAIR_SYSTEM},
            {"role": "user", "content": body},
        ]
        log("  定向修复 " + rel + "（" + str(len(vs)) + " 条问题）…")
        raw = chat(cfg, msgs, stage="repair-" + rel.replace("/", "_"), log=log)
        files = parse_files(raw)
        if not files:
            log("  ⚠️ " + rel + " 的修复没返回文件，跳过")
            continue
        if rel not in files:
            hit = next((k for k in files if k.endswith(rel.split("/")[-1])), None)
            if hit:
                files[rel] = files.pop(hit)
            else:
                log("  ⚠️ " + rel + " 的修复没返回该文件，跳过")
                continue
        allowed = {rel: files[rel]}
        for k, v in files.items():
            if k == rel:
                continue
            if (output_dir / k).exists():
                log("  ⚠️ 修复想改既有文件 " + k + "（未被点名），已忽略")
            else:
                allowed[k] = v
                log("  ＋ 修复附带新建 " + k)
        written += write_files(output_dir, allowed, log=log)
    return written
