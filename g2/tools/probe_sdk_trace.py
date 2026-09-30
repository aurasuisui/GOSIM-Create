import json
import sys
from pathlib import Path

SDK = Path(sys.argv[2])
sys.path.insert(0, str(SDK))
from arcbench_agent_runtime import AgentRuntime

work = Path(sys.argv[1])
work.mkdir(parents=True, exist_ok=True)
rt = AgentRuntime.from_env(project_dir=str(work))
rt.traceability.init_db()
rt.traceability.upsert_requirement(req_id="REQ-1", name="Demo", description="d",
                                  scenarios=[{"id": "REQ-1-SCN-1", "name": "s",
                                              "steps": [{"keyword": "GIVEN", "content": "x"}]}])
rt.traceability.upsert_interface(interface_id="REQ-1.API.Demo", req_ids=["REQ-1"], type="api",
                                 content="GET /api/demo", file_path="backend/src/routes/demo.js",
                                 implemented=True)
rt.traceability.upsert_test(test_id="REQ-1.E2E", req_id="REQ-1", type="E2E")
rt.traceability.upsert_node_state("REQ-1", "IMPLEMENTED", "implement")
rt.events.mark_design_done("REQ-1", "ok")
rt.events.mark_implementation_done("REQ-1", "ok")

print("=== SDK 写的文件 ===")
for f in sorted((work / ".arc" / "traceability").glob("*.json")):
    data = json.loads(f.read_text(encoding="utf-8"))
    print(f.name, "->", json.dumps(data, ensure_ascii=False)[:200])
ev = work / ".arc" / "runner-events.jsonl"
print("=== runner-events ===")
print(ev.read_text(encoding="utf-8")[:360] if ev.is_file() else "(none)")