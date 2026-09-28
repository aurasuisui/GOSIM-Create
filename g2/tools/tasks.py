"""任务表：把"哪个 app 用哪套官方测试打分"集中在一处。

出处：平台 /requirements 目录 + 本地 repos/arc-bench/arc-bench/webapp/<app>/tests。
⚠️ 官方 hackathon 任务（hackathon--github / hackathon--sheet）的测试**平台不公开**
（/requirements/<id>/tests?catalog=competition → 404）；公开可判的是 Lite 的这两个任务。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Task:
    key: str              # 平台任务 id
    app: str              # 本地测试目录名
    title: str
    tests: int            # 官方测试条数（平台标注）
    tests_dir: Path       # 本地官方测试目录（只读，来自 repos/）
    pack_dir: Path        # 需求包目录（g2/packs/<app>）


ROOT = Path(__file__).resolve().parents[2]          # 工作区根
REPO_TESTS = ROOT / "repos/arc-bench/arc-bench/webapp"

TASKS: dict[str, Task] = {
    "bookstack": Task(
        key="arc-bench-lite--bookstack", app="bookstack",
        title="BookStack Knowledge Base System", tests=34,
        tests_dir=REPO_TESTS / "bookstack/tests",
        pack_dir=ROOT / "g2/packs/bookstack"),
    "keep": Task(
        key="arc-bench-lite--keep", app="keep",
        title="Keep", tests=32,
        tests_dir=REPO_TESTS / "keep/tests",
        pack_dir=ROOT / "g2/packs/keep"),
}


def get(app: str) -> Task:
    if app not in TASKS:
        raise KeyError(f"未知 app：{app}（可选：{', '.join(TASKS)}）")
    return TASKS[app]