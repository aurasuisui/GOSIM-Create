"""打包提交包：`<工作区>/output/bundle-g2.zip`。

红线（每条都有出处，见 .rebuild/LESSONS.md §2）：
  1. **zip 第一层必须直接是 `main.py`**（不能套一层目录）；
  2. **根层不能有 `package.json` / `index.js` / `index.ts`**（会触发 runner 的 Node 入口路径）；
     ⚠️ 只管**根层** —— `templates/.../backend/package.json` 这类子目录的必须存在；
  3. **不塞 `.env`** / `.git/` / `node_modules/` / `__pycache__/` / `*.db` / `work/` / `runs/`；
  4. **不得预埋答案**：包里不许出现题目特定字符串（`tools/check_bundle_strings.py` 机器判）。
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "g2"

INCLUDE_FILES = ["main.py", "requirements.txt"]
INCLUDE_DIRS = ["app", "templates"]
SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".git", "work", "runs", "packs", "dist"}
SKIP_SUFFIX = {".pyc", ".pyo", ".db", ".log", ".zip"}
FORBIDDEN_ROOT = {"package.json", "index.js", "index.ts"}


def iter_files() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for name in INCLUDE_FILES:
        p = SRC / name
        if p.is_file():
            out.append((p, name))
    for d in INCLUDE_DIRS:
        base = SRC / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            if p.suffix in SKIP_SUFFIX:
                continue
            rel = p.relative_to(SRC).as_posix()
            out.append((p, rel))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "output" / "bundle-g2.zip"))
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    files = iter_files()
    names = [rel for _p, rel in files]
    roots = {n.split("/")[0] for n in names}
    bad = sorted(roots & FORBIDDEN_ROOT)
    if bad:
        raise SystemExit(f"❌ 根层出现禁止的文件：{bad}（会触发 runner 的 Node 入口）")
    if "main.py" not in names:
        raise SystemExit("❌ 包里没有 main.py（平台按它当入口）")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p, rel in files:
            z.write(p, rel)
    print(f"✓ {out}  ({len(files)} 个文件, {out.stat().st_size / 1024:.1f} KB)")
    print("  根层：" + ", ".join(sorted(roots)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())