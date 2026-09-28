"""构建体检：前端 build + 后端 node --check，并**把构建错误按文件报出来**。

为什么需要（实测）：
  ① 一轮定向修复把某个页面重写成语法错 → **前端构建直接失败** → 那一整轮判分作废
     （而读数看起来像"页面做得不好"）；
  ② 另一类后端语法错会让**服务起不来**，表现为大量 `ERR_CONNECTION_REFUSED`；
  ③ 构建错误其实**带着文件名与行号**（esbuild 会打 `path:line:col: ERROR: ...`）→
     所以它可以被当成一条"可修复的 finding"喂回定向修复，而不是白丢一轮。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

NPM = "npm.cmd" if os.name == "nt" else "npm"
# ⚠️ 路径里**可能带空格**（实测工作区路径含空格）→ 不能用 `\S` 断词，
#    要非贪婪地吃到 `.tsx` 再看 `:行:列: ERROR:`。
RE_ESBUILD = re.compile(r"([^\n]*?\.(?:tsx?|jsx?)):(\d+):(\d+):\s*ERROR:\s*([^\n]{0,160})")


def backend_syntax_ok(out_dir: Path) -> tuple[bool, str]:
    for p in sorted((Path(out_dir) / "backend" / "src").rglob("*.js")):
        proc = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return False, "node --check 失败：" + p.name + " " + (proc.stderr or "")[:200]
    return True, ""


def frontend_build(out_dir: Path, timeout: int = 900) -> tuple[bool, str]:
    front = Path(out_dir) / "frontend"
    if not (front / "package.json").is_file():
        return True, "no frontend"
    # ⚠️ 没装依赖就构建不了，**这不等于"构建失败"**：实测在 install 之前跑这个检查一律失败，
    #    而"安全网"会把刚修好的文件又原样回退掉（白折腾）。所以这里跳过并明说。
    if not (front / "node_modules").is_dir():
        return True, "skipped: node_modules missing"
    env = dict(os.environ, npm_config_allow_scripts="")
    proc = subprocess.run([NPM, "run", "build"], cwd=str(front), capture_output=True, text=True,
                          timeout=timeout, env=env)
    if proc.returncode != 0:
        # ⚠️ 必须返回**完整输出**：esbuild 的 `path:line:col: ERROR:` 在**开头**，
        #    只留尾部就解析不到文件（实测踩过：`broken_files` 返回空 dict）。
        return False, (proc.stdout or "") + (proc.stderr or "")
    return True, ""


def broken_files(out_dir: Path, build_output: str) -> dict[str, str]:
    """从 esbuild 的报错里抠出 `文件 → 原因`（相对 out_dir 的 posix 路径）。"""
    out_dir = Path(out_dir)
    found: dict[str, str] = {}
    for m in RE_ESBUILD.finditer(build_output or ""):
        path, line, col, msg = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            rel = Path(path).resolve().relative_to(out_dir.resolve()).as_posix()
        except Exception:  # noqa: BLE001
            continue
        found[rel] = f"构建失败（{line}:{col}）：{msg}"
    return found


def build_ok(out_dir: Path) -> tuple[bool, str]:
    ok, why = backend_syntax_ok(out_dir)
    if not ok:
        return False, why
    return frontend_build(out_dir)