"""从需求**散文**里抽 ARIA 契约（官方题把可访问性要求写在描述正文里）。

为什么单列（2026-09-28 实测官方需求）：
  某个官方题的描述里写着"worksheet tabs ... use the **ARIA tab role**,
   with the active tab indicated by **aria-selected="true"**; the active worksheet grid
   uses the **ARIA grid role**, has the **accessible name "Worksheet grid"**, and exposes
   **aria-multiselectable="true"**; grid cells use the **ARIA gridcell role** with their
   cell coordinates as accessible names (for example, A1)"。
  → 判据就是照这些句子写的；而我们的抽取器只认引号里的名字，**整段角色契约都没进产物**。
"""
from __future__ import annotations

import re

RE_ROLE_WORD = re.compile(r"(?:the\s+)?ARIA\s+([a-z][\w-]*)\s+role", re.I)
RE_ROLE_ATTR = re.compile(r"role\s*=\s*[\"\']([a-z][\w-]*)[\"\']", re.I)
RE_ACCNAME = re.compile(r"accessible name\s*[\"\u201c\']([^\"\u201d\']{1,40})[\"\u201d\']", re.I)
RE_ARIA_ATTR = re.compile(r"(aria-[\w-]+)\s*=\s*[\"\']([^\"\']{1,24})[\"\']", re.I)
RE_EXAMPLE = re.compile(r"\(for example,\s*([A-Z]\d{1,3})\)", re.I)


def aria_contracts(text: str) -> list[dict]:
    """返回 [{role, name, attrs, in_role, note}]（按角色去重）。"""
    if not text:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in RE_ROLE_WORD.finditer(text):
        role = m.group(1).lower()
        window = text[max(0, m.start() - 260): m.end() + 320]
        name = ""
        nm = RE_ACCNAME.search(window)
        if nm:
            name = nm.group(1).strip()
        attrs = {a.lower(): v for a, v in RE_ARIA_ATTR.findall(window)}
        key = (role, name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"role": role, "name": name, "attrs": attrs,
                    "note": window.strip()[:160]})
    return out


def example_cells(text: str) -> list[str]:
    return RE_EXAMPLE.findall(text or "")