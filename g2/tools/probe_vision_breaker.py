import json, os, sys
from pathlib import Path
sys.path.insert(0, "g2")
os.environ["G2_VISION"] = "1"
from app.gen.design import Blueprint
from app.gen.llm import LLMConfig, Ledger
from app.gen.writer import FileJob, write_file
from app.reqcomp.loader import load_pack
from app.reqcomp.planner import group_requirements
out = Path(sys.argv[1])
tree = load_pack(sys.argv[2])
groups = group_requirements(tree)
bp_data = json.loads((out / ".arc" / "g2-blueprint.json").read_text(encoding="utf-8"))
bp = Blueprint(**{k: v for k, v in bp_data.items() if k in {"routes","api_endpoints","db_tables","auth","error_shape","notes"}})
cfg = LLMConfig.from_env(output_dir=out)
ledger = Ledger(out)
job = FileJob(path="frontend/src/pages/CreateWorkbookPage.tsx", kind="page", purpose="测试视觉熔断", req_ids=[])
r = write_file(cfg, job, bp, groups, out, existing=[], ledger=ledger, log=print)
print("RESULT:", json.dumps(r, ensure_ascii=False)[:200])
from app.gen import llm
print("VISION_DISABLED after:", llm.VISION_DISABLED)