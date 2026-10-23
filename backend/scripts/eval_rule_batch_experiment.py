"""VLM 规则批量实验：单臂运行器。复用生产链路（EvidenceSession+run_agent+组件工具）。"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, "backend")
import httpx
from app.modules.video_model.evidence.tools import EvidenceSession
from app.modules.video_model.evidence.agent import run_agent
from app.modules.workflow.video_components import freeze_plan, ComponentPlan
from app.modules.workflow.prompts import prompt
from app.modules.video_model.evidence.secrets import cipher
import sqlite3

BASE = Path("/tmp/exp2")
db = sqlite3.connect("backend/.data/workbench.sqlite3"); db.row_factory = sqlite3.Row
cfg = db.execute("SELECT endpoint,model,encrypted_key FROM evidence_model_settings WHERE id=1").fetchone()
SECRET = cipher(Path("backend/.data")).decrypt(cfg["encrypted_key"].encode()).decode()
ENDPOINT, MODEL = cfg["endpoint"].rstrip("/"), cfg["model"]

def node(cid, kind, params):
    return {"id": cid, "kind": kind, "parameters": params}

def plan(nodes):
    return freeze_plan(ComponentPlan.model_validate({"operator": "all", "nodes": nodes}))

RULES = {
 "r1": {"id":"video-r1","ruleNumber":1,"text":"视频中必须出现产品（芝士奶铁杯体），允许杯身被贴纸或色块遮挡","category":"产品露出","importance":"must","reviewStages":["video"],
        "componentPlan": plan([node("c1","product_presence",{"reference_image_ids":["ref-1"],"allow_occlusion":True})])},
 "r2": {"id":"video-r2","ruleNumber":2,"text":"产品杯身商标必须清晰可见，不得被贴纸、色块或手指遮挡","category":"商标露出","importance":"must","reviewStages":["video"],
        "componentPlan": plan([node("c1","logo_presence",{"reference_image_ids":["ref-1"],"require_clear_logo":True})])},
 "r3": {"id":"video-r3","ruleNumber":3,"text":"禁止出现表达：提神醒脑（口播、字幕、画面文字均算）","category":"禁用表达","importance":"must","reviewStages":["video"],
        "componentPlan": plan([node("c1","forbidden_expression",{"terms":["提神醒脑"]})])},
 "r4": {"id":"video-r4","ruleNumber":4,"text":"口播必须包含：麦咖啡冰奶铁","category":"必须口播","importance":"must","reviewStages":["video"],
        "componentPlan": plan([node("c1","required_speech",{"terms":["麦咖啡冰奶铁"]})])},
 "r5": {"id":"video-r5","ruleNumber":5,"text":"产品累计露出时长不得少于5秒","category":"画面","importance":"must","reviewStages":["video"],
        "componentPlan": plan([node("c1","exposure_duration",{"reference_image_ids":["ref-1"],"min_total_seconds":5,"min_continuous_seconds":1,"allow_occlusion":True})])},
 "r6": {"id":"video-r6","ruleNumber":6,"text":"达人必须做出举杯向镜头展示产品的动作","category":"指定动作","importance":"must","reviewStages":["video"],
        "componentPlan": plan([node("c1","specified_action",{"action":"举杯向镜头展示产品"})])},
}

ARMS = {"K6": ["r1","r2","r3","r4","r5","r6"], "K3a": ["r1","r2","r5"], "K3b": ["r3","r4","r6"], "K1_r2": ["r2"], "K1_r3": ["r3"]}

def complete(messages, tools):
    body = {"model": MODEL, "messages": messages, "stream": False}
    if tools: body["tools"] = tools; body["parallel_tool_calls"] = False
    r = httpx.post(ENDPOINT + "/chat/completions", headers={"Authorization": "Bearer " + SECRET}, json=body, timeout=280, follow_redirects=False)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()

def main(video_key, arm_key, repeat=0):
    video = BASE / f"{video_key}.mp4"
    ref = BASE / "ref.png"
    references = [{"id": "ref-1", "sha256": __import__("hashlib").sha256(ref.read_bytes()).hexdigest(), "data": ref.read_bytes()}]
    rules = [RULES[k] for k in ARMS[arm_key]]
    out = BASE / "results" / f"{video_key}_{arm_key}_x{repeat}"
    session = EvidenceSession(video=video, output_dir=out, ffmpeg=__import__("imageio_ffmpeg").get_ffmpeg_exe(),
                              rules=rules, script="（实验：无脚本上下文）", references=references)
    t0 = time.time()
    record = {"video": video_key, "arm": arm_key, "repeat": repeat, "rules": ARMS[arm_key], "seconds": None, "ok": False}
    try:
        result = run_agent(session, complete, system_prompt=prompt("video-review")["content"], final_prompt=prompt("video-final-report")["content"])
        record.update(ok=True, usage=result.get("usage"), providerRequestIds=result.get("providerRequestIds"),
                      report=result["report"],
                      components=[{k: c.get(k) for k in ("ruleId","componentId","kind","verdict","reason","coverageClaimed","observations","intervals","metrics")} for c in result["componentResults"]],
                      toolCalls=result["toolCalls"], artifacts=[{k:a.get(k) for k in ("id","kind","issueId","startMs","endMs")} for a in session.artifacts])
    except Exception as exc:
        record.update(errorType=type(exc).__name__, error=str(exc)[:400],
                      components=[{k: c.get(k) for k in ("ruleId","componentId","kind","verdict","reason","coverageClaimed","observations","intervals","metrics")} for c in session.component_results.values()],
                      toolCalls=session.calls, artifacts=[{k:a.get(k) for k in ("id","kind","issueId","startMs","endMs")} for a in session.artifacts])
    record["seconds"] = round(time.time() - t0, 1)
    (out / "run_record.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(video_key, arm_key, "x"+str(repeat), "ok" if record["ok"] else record.get("errorType"), record["seconds"], "s | comps:", [(c["ruleId"], c["verdict"]) for c in record.get("components", [])])

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
