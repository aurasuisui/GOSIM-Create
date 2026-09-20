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

from generate.llm import LLMConfig, chat
from generate.implement import parse_files, write_files

REVIEW_SYSTEM = ("你是严格的需求验收审查员。只列**能被外部测试抓到的具体缺陷**，"
                 "不要提风格建议，不要对没看到的代码做猜测。只输出 JSON 数组。")

REPAIR_SYSTEM = ("你是资深全栈工程师。只修**被指出的缺陷**，不要顺手重构。"
                 "必须输出完整文件，且严格遵守 ===FILE: 格式。"
                 "改到 import 时**必须核对相对层级**（数一下从本文件所在目录到目标文件的目录深度）"
                 "——写错相对路径会让构建直接失败。")

MAX_CHARS_PER_FILE = 40000


def _list_code(output_dir) -> tuple[str, list[str]]:
    """把生成出来的源码整理成可读清单（供审查）。文件全量给出，不做小截断。"""
    files: list[str] = []
    for top in ("frontend/src", "backend/src"):
        base = output_dir / top
        if base.is_dir():
            files += [f for f in sorted(base.rglob("*"))
                      if f.is_file() and f.suffix in (".ts", ".tsx", ".js")
                      and "database/" not in f.as_posix()]
    blocks, names = [], []
    for f in files:
        rel = f.relative_to(output_dir).as_posix()
        names.append(rel)
        text = f.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_CHARS_PER_FILE:
            text = (text[:MAX_CHARS_PER_FILE]
                    + "\n…(此文件过长已截断；**不要对看不到的部分做任何猜测**)")
        blocks.append("===FILE: " + rel + "===\n```\n" + text + "\n```")
    return "\n\n".join(blocks), names


def review(output_dir, requirement_brief: str, l1_findings: list[dict],
           cfg: LLMConfig, *, log=print) -> list[dict]:
    """要模型列出"哪些地方对不上需求"。返回 [{"file","problem","fix"}]。"""
    code, _ = _list_code(output_dir)
    l1_text = "\n".join("  - [" + f["kind"] + "] " + f["file"] + ": " + f["detail"]
                        for f in l1_findings) or "  （无）"
    body = """下面是**需求**与刚生成的**代码**（全文）。请找出代码里**对不上需求**的具体缺陷。

## 需求（验收依据，逐字读；注意关于「页面显示什么 / 怎么进入 / 叫什么名字」的要求）

""" + requirement_brief + """

## 静态检查已发现的问题（可能不完整）

""" + l1_text + """

## 生成的代码

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

⚠️ **只根据你确实看到的代码下结论**。文件若被截断，**不要猜「可能缺失」**——
猜测会被当成真缺陷去改代码。
⚠️ **不要提「可以更好」这类建议**，只列会让外部测试失败的东西。"""
    msgs = [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user", "content": body},
    ]
    raw = chat(cfg, msgs, stage="selfcheck-review", log=log)
    return parse_violations(raw, log=log)


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
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("file") and item.get("problem"):
            out.append({"file": str(item["file"]).lstrip("./"),
                        "problem": str(item["problem"]),
                        "fix": str(item.get("fix", ""))})
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

    written: list[str] = []
    for rel, vs in list(by_file.items())[:max_files]:
        target = output_dir / rel
        if not target.is_file():
            log("  ⚠️ 自检点名了不存在的文件 " + rel + "，跳过")
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_CHARS_PER_FILE:
            text = text[:MAX_CHARS_PER_FILE] + "\n…(已截断；不要猜看不到的部分)"
        problems = "\n".join(
            "  " + str(i + 1) + ". 问题：" + v["problem"] + "\n     建议改法：" + v["fix"]
            for i, v in enumerate(vs))
        inventory = "\n".join("  - " + n for n in existing_list) or "  （无）"
        body = ("这个文件被验收审查点名了，请**只修这些缺陷**。\n\n"
                "文件：`" + rel + "`\n\n"
                "被指出的问题（逐条修掉，不要顺手改别的）：\n" + problems + "\n\n"
                "当前内容：\n```\n" + text + "\n```\n\n"
                "## 项目里**已经存在**的文件（import 只能指向这些，或你本次一并新建的）\n\n"
                + inventory + "\n\n"
                "## 输出格式（严格遵守）\n\n"
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
