"""把「**产物出生那一刻**」的指纹写进产物本身：`<output_dir>/.arc/provenance.json`。

**为什么必须写进产物**（第二十三轮审核 §三 的根因，2026-09-22）：

`RunRecord` 里的 `pipeline.git_commit` / `git_dirty` 原本是在**写记录时**（判分跑完、约一小时后）
用 `git status` 现采的。于是出现过一个真实的自相矛盾：

  · 产物 16:30 生成（代码 = `b3c497e`，`pipeline/` 干净）
  · 记录 17:33 落盘 → `git_dirty = true`（因为那时工作区里有文档/记录的改动）
  → **指纹 attest 的是 17:33 的工作区，不是 16:30 的产物。**

后果不是"信息少一点"，而是**记录无法自证**：按 `docs/04` §一 的机械口径筛，
这一轮会被当成"污染轮次"丢掉——而它是当时的头条结果。

→ 所以指纹要**盖在产物出生处**：生成阶段结束时写这个文件；`eval/extract_run.py`
**优先读它**（读不到才退回"写记录时现采"，并把这个来源写进记录）。

字段（`extract_run.py` 直接读 JSON，不 import 本模块——保持解耦）：

    {
      "schema": "pipeline-provenance/1",
      "written_at": "2026-09-22T08:30:36Z",     # 产物出生时刻（UTC）
      "written_at_local": "2026-09-22 16:30:36",
      "git_commit": "b3c497e…",                 # 产出它的那份**管线**代码
      "git_dirty": false,                      # 整个仓库（可能只是文档/记录在飞）
      "pipeline_dirty": false,                 # **只看 pipeline/ 的代码** —— 这个才是"产出它的代码"干净与否
      "git_branch": "main",
      "requirement_sha256": "20fd6a60…",        # 输入指纹
      "app_hint": "<app 名，来自需求路径末段>",
      "req_ids": ["REQ-2.1", "REQ-2.2"],        # 子集（空 = 全量）
      "generate_mode": "",                      # PIPELINE_GENERATE 的取值
      "stage_seconds": 28.1,                    # 第二次写入才有
      "tokens_generation_only": 15833,          # 生成段（设计 + 各块实现）
      "tokens_total": 37290,                    # **全程**（生成段 + 验证闭环）
      "verify_ran": true
    }

⚠️ **`tokens_total` 必须等验证闭环跑完再盖**（第二十四轮审核 §三 A）：闭环占全程 **51–58%**
（闭环自己那轮实测的核心量、`pass/CNY` 的分母），在闭环**之前**盖会把"总 token"写成全程的 42%（实测 15,833）。
→ 所以两个数**分开命名**，别让一个字段同时表示两件事；没有闭环时两者相等。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "pipeline-provenance/1"
REL_PATH = Path(".arc") / "provenance.json"


def _git(repo: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:  # noqa: BLE001 —— 不是仓库/没有 git 都不该让生成失败
        return None


def _sha256(path: Path) -> str | None:
    if not path or not Path(path).is_file():
        return None
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(*, repo: Path | None = None, requirement_path=None,
            extra: dict | None = None) -> dict:
    """采一份指纹（纯读，不写）。"""
    repo = Path(repo) if repo else Path(__file__).resolve().parent
    commit = _git(repo, "rev-parse", "HEAD")
    now = datetime.now(timezone.utc)
    # 「仓库脏」与「**产出产物的那份代码**脏」是两件事：实测过多次"仓库脏但只是文档/记录在飞"，
    # 而它会把整轮读数读成"污染轮"（第二十三轮审核 §三）。所以单列一个只管道线代码的字段。
    pipe_path = Path(__file__).resolve().parent
    pipeline_dirty = None
    if commit:
        porcelain = _git(repo, "status", "--porcelain", "--", str(pipe_path))
        pipeline_dirty = bool(porcelain)
    data: dict = {
        "schema": SCHEMA,
        "written_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "written_at_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": commit,
        "git_dirty": bool(_git(repo, "status", "--porcelain")) if commit else None,
        # 只要这一个为 False，"产出这份产物的代码"就是干净的（仓库其它地方可能只是文档在飞）
        "pipeline_dirty": pipeline_dirty,
        "git_branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        # 🔴 **为什么要有 subject**（第三十轮审核 §三 A）：`git_commit` 指着一个 hash，
        # 而 hash 会被下一次**历史重排 / 阶段合并**换掉 —— 实测：21 份带指纹的记录里 hash
        # **全部不可达**（含刚落盘的那些）。而 `subject` 会被阶段合并**抄进提交正文**，能跨重排活下来。
        # 所以溯源要两个都记：hash 用于"当下精确定位"，subject 用于"以后还认得出来"。
        "git_subject": _git(repo, "log", "-1", "--format=%s"),
        "requirement_sha256": _sha256(Path(requirement_path)) if requirement_path else None,
    }
    if extra:
        data.update(extra)
    return data


def write(output_dir: Path, *, requirement_path=None, extra: dict | None = None,
          log=print) -> dict:
    """写 `<output_dir>/.arc/provenance.json`。**幂等覆盖**（最后一次调用生效）。"""
    data = collect(requirement_path=requirement_path, extra=extra)
    target = Path(output_dir) / REL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  🧾 产物指纹已盖在出生处：{REL_PATH}"
        f"（commit={(data.get('git_commit') or '?')[:8]}"
        f"，dirty={data.get('git_dirty')}）")
    return data


def read(output_dir: Path) -> dict | None:
    """读回指纹；没有/坏了都返回 None（调用方自己决定怎么退化）。"""
    target = Path(output_dir) / REL_PATH
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


# ---- 独立自检（`python pipeline/provenance.py <产物目录>`）----
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python pipeline/provenance.py <output_dir>")
        raise SystemExit(2)
    got = read(Path(sys.argv[1]))
    print(json.dumps(got, ensure_ascii=False, indent=2) if got else "（没有 provenance.json）")
