from pathlib import Path
import re, sys
sys.path.insert(0, "g2")

text = Path("g2/packs/bookstack/tests/helpers.ts").read_text(encoding="utf-8")
# 两处来源：① clickNamed(page, /^X$/i) 或 clickNamed(page, 'X')；② getByRole('button', { name: /^X$/i })
names = []
names += re.findall(r"clickNamed\(\s*[^,]+,\s*/\^?([^/]{1,40}?)\$?/i", text)
names += re.findall(r"clickNamed\(\s*[^,]+,\s*['\"]([^'\"]{1,40})['\"]", text)
names += re.findall(r"getByRole\(\s*['\"](button|link|tab)['\"]\s*,\s*\{\s*name:\s*/\^?([^/]{1,40}?)\$?/i", text)
print("clickNamed names:", sorted(set(n.strip() for n in names if isinstance(n, str) and 1 < len(n.strip()) < 40))[:25])
roles = re.findall(r"getByRole\(\s*['\"]([a-z]+)['\"]", text)
import collections
print("roles used by helpers:", dict(collections.Counter(roles)))