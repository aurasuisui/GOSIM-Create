from pathlib import Path
import sys
sys.path.insert(0, "g2")
from app.gen.seed import load_fixtures, flatten, write_seed
for app in ("bookstack", "keep"):
    fx = load_fixtures(Path(f"g2/packs/{app}/tests"))
    info = write_seed(Path(f"g2/work/seedprobe-{app}"), fx)
    print(app, info)