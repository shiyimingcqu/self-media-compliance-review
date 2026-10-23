from __future__ import annotations

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.core.database import connection
from app.core.eligibility import applicable, rules_enabled
from app.core.errors import ApiError
from app.modules.foundation.files_service import get_asset
from app.modules.foundation.service import now
from .console_schemas import EvidenceRunInput, EvidenceRunView, ModelSettingsInput, ModelSettingsView
from .secrets import cipher

from .temporary_upload import VideoUploadFailure, transport_mode, upload_video

DEFAULT_ENDPOINT = "https://maas.qianwenaiapi.com/compatible-mode/v1"


def settings_view(settings: Settings) -> ModelSettingsView:
    with connection(settings.database_path) as db:
        row = db.execute("SELECT * FROM evidence_model_settings WHERE id=1").fetchone()
    return ModelSettingsView(endpoint=row["endpoint"] if row else DEFAULT_ENDPOINT,
                             model=row["model"] if row else "qwen3.8-omni-flash",
                             has_key=bool(row and row["encrypted_key"]), revision=row["revision"] if row else 0,
                             updated_at=row["updated_at"] if row else None,
                             max_inline_bytes=None)


def save_settings(settings: Settings, payload: ModelSettingsInput) -> ModelSettingsView:
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM evidence_model_settings WHERE id=1").fetchone()
        if payload.revision != (row["revision"] if row else 0):
            raise ApiError(409, "REVISION_CONFLICT", "模型配置已更新，请刷新后重试")
        encrypted = row["encrypted_key"] if row else None
        if payload.clear_key:
            encrypted = None
        elif payload.api_key is not None:
            encrypted = cipher(settings.data_dir).encrypt(payload.api_key.encode()).decode()
        # Retain the constrained legacy DB column for compatibility only; it no longer gates sending.
        db.execute("INSERT INTO evidence_model_settings (id,endpoint,model,encrypted_key,revision,updated_at,max_inline_bytes) VALUES (1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "endpoint=excluded.endpoint,model=excluded.model,encrypted_key=excluded.encrypted_key,"
                   "revision=excluded.revision,updated_at=excluded.updated_at,max_inline_bytes=excluded.max_inline_bytes",
                   (payload.endpoint, payload.model, encrypted, payload.revision + 1, now(), row["max_inline_bytes"] if row else 7_000_000))
    return settings_view(settings)


def _run_view(row) -> EvidenceRunView:
    return EvidenceRunView(id=row["id"], kind=row["kind"], task_id=row["task_id"], file_id=row["file_id"],
        status=row["status"], config_revision=row["config_revision"], snapshot=json.loads(row["snapshot_json"]),
        result=json.loads(row["result_json"]) if row["result_json"] else None, error_code=row["error_code"],
        error_message=row["error_message"], created_at=row["created_at"], updated_at=row["updated_at"],
        affects_workflow=bool(json.loads(row["snapshot_json"]).get("workflowContextKey")))


def get_run(settings: Settings, run_id: str) -> EvidenceRunView:
    with connection(settings.database_path) as db:
        row = db.execute("SELECT * FROM evidence_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "调用记录不存在")
    return _run_view(row)


def list_runs(settings: Settings, task_id: str | None) -> list[EvidenceRunView]:
    with connection(settings.database_path) as db:
        if task_id:
            rows = db.execute("SELECT * FROM evidence_runs WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 20", (task_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM evidence_runs WHERE task_id IS NULL ORDER BY created_at DESC,id DESC LIMIT 20").fetchall()
    return [_run_view(row) for row in rows]


def prepare_run(settings: Settings, payload: EvidenceRunInput, key: str):
    """同事务幂等创建，明文密钥只返回给本进程工作线程，不进入快照/作业库。"""
    import hashlib
    if not payload.allow_real_call:
        raise ApiError(400, "REAL_CALL_NOT_CONFIRMED", "请明确确认真实调用及可能产生的费用")
    fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    scope = "local-reviewer:evidence-real-call"
    with connection(settings.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT * FROM idempotency_records WHERE scope=? AND key=?", (scope, key)).fetchone()
        if previous:
            if previous["request_hash"] != fingerprint:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于其他请求")
            identity = json.loads(previous["response_json"])["id"]
            return identity, None
        config = db.execute("SELECT * FROM evidence_model_settings WHERE id=1").fetchone()
        if not config or not config["encrypted_key"]:
            raise ApiError(409, "MODEL_KEY_MISSING", "请先在模型设置保存 API Key")
        if config["revision"] != payload.config_revision:
            raise ApiError(409, "REVISION_CONFLICT", "模型配置已变化，请重新确认调用")
        if db.execute("SELECT 1 FROM evidence_runs WHERE status IN ('queued','processing') LIMIT 1").fetchone():
            raise ApiError(409, "EVIDENCE_RUN_BUSY", "已有真实调用正在运行，请等待结束，避免重复计费")
        frozen = {"mode": "workflow" if payload.task_id else "real-test", "configRevision": config["revision"], "model": config["model"],
                  "endpoint": config["endpoint"], "maxInlineBytes": None, "affectsWorkflow": bool(payload.task_id)}
        from app.modules.workflow.prompts import prompt
        frozen['prompts'] = ({'video':prompt('video-review'),'final':prompt('video-final-report')} if payload.kind=='video' else {'connection':prompt('connection-test')})
        if payload.kind == 'video':
            from .localized import VERSION
            frozen['pipelineVersion'] = VERSION
            frozen['prompts'].update(localize=prompt('video-localize'), refine=prompt('video-refine'))
        source_path = None
        rules = []
        if payload.kind == "video":
            if not payload.file_id:
                raise ApiError(400, "VALIDATION_ERROR", "请选择完整视频文件")
            asset, source_path = get_asset(settings, payload.file_id)
            if asset.purpose != "video" or asset.extension != "mp4":
                raise ApiError(415, "UNSUPPORTED_MEDIA", "当前取证入口只支持完整 MP4 视频")
            frozen.update({"fileId": asset.id, "sourceSha256": asset.sha256})
            frozen['compressionPreset'] = payload.compression_preset
            frozen['videoTransport'] = transport_mode(config['endpoint'])
            if payload.task_id:
                eligibility = rules_enabled(db, payload.task_id)
                if not eligibility.allowed:
                    raise ApiError(409, eligibility.code, eligibility.message)
                task = db.execute("SELECT * FROM creator_tasks WHERE id=?", (payload.task_id,)).fetchone()
                from app.modules.workflow.router import video_submission_context
                workflow, script_text = video_submission_context(db, payload.task_id)
                frozen.update({'workflowContextKey': workflow['contextKey'],
                               'scriptApprovalId': workflow['scriptApproval']['id'] if workflow['scriptApproval'] else None,
                               'scriptVersionId': workflow['currentScript']['scriptVersionId'] if workflow['currentScript'] else None,
                               'approvedScript': script_text, 'scriptStatus': 'brand_approved' if workflow['scriptApproval'] else 'pending_approval' if workflow['currentScript'] else 'not_submitted'})
                # 同一个不可变文件不得被其他项目的取证记录借用。
                cross = db.execute("SELECT 1 FROM evidence_runs r JOIN creator_tasks t ON t.id=r.task_id "
                                   "WHERE r.file_id=? AND t.project_id<>? LIMIT 1", (asset.id, task["project_id"])).fetchone()
                if cross:
                    raise ApiError(409, "FILE_ALREADY_REGISTERED", "该视频已关联其他项目")
                from app.modules.workflow.router import applicable_rules
                rules = applicable_rules(db, task, 'video')
                frozen.update({"taskId": task["id"], "projectId": task["project_id"],
                               "taskRevision": task["revision"], "ruleVersionId": task["rule_version_id"],
                               "scriptStandardSetId": workflow["standardSets"]["script"]["id"], "videoStandardSetId": workflow["standardSets"]["video"]["id"]})
            else:
                rules = [{"id": "capability-test-rule", "text": payload.test_rule, "source": "用户显式测试规则"}]
            frozen["rules"] = rules
            if payload.task_id:
                from app.modules.workflow.video_layers import runnable, review_level
                rules, pending_configuration = runnable(rules, bool(frozen.get('approvedScript')))
                frozen['unconfiguredRules'] = pending_configuration
                frozen['videoReviewLevel'] = review_level(frozen['rules'])
                frozen['executableRuleIds'] = [r['id'] for r in rules]
                if not rules:
                    raise ApiError(409, 'VIDEO_CONFIGURATION_REQUIRED', '启用的规则均待品牌配置，尚未发送模型；请补齐资料或调整启用项')
            if not rules or len(json.dumps(rules, ensure_ascii=False)) > 100_000:
                raise ApiError(400, "RULES_INVALID", "规则为空或超出本次输入预算")
            reference_ids={ref for r in rules for n in (r.get('componentPlan') or {}).get('nodes',[]) for ref in n['parameters'].get('referenceImageIds',[])}
            if len(reference_ids)>8:
                raise ApiError(400,'REFERENCE_BUDGET_LIMIT','每次审核最多8张参考图')
            references=[]
            if reference_ids:
                from app.modules.workflow.reference_images import get_reference
                for ref in sorted(reference_ids):
                    references.append(get_reference(db,settings,frozen['projectId'],ref))
                if sum(ref['byte_size'] for ref in references)>8*1024*1024:
                    raise ApiError(400,'REFERENCE_BUDGET_LIMIT','参考图总量须在8MiB以内')
            frozen['references']=references
            frozen.setdefault("scriptStatus", "not_provided_no_verified_approval")
        elif payload.file_id or payload.task_id:
            raise ApiError(400, "VALIDATION_ERROR", "文本连接测试不接收视频或任务")
        identity, stamp = uuid4().hex, now()
        db.execute("INSERT INTO evidence_runs (id,kind,task_id,file_id,status,config_revision,snapshot_json,created_at,updated_at) "
                   "VALUES (?,?,?,?,'queued',?,?,?,?)", (identity, payload.kind, payload.task_id, payload.file_id,
                   config["revision"], json.dumps(frozen, ensure_ascii=False), stamp, stamp))
        db.execute("INSERT INTO idempotency_records VALUES (?,?,?,?,?)", (scope, key, fingerprint, json.dumps({"id": identity}), stamp))
        if payload.task_id:
            db.execute("UPDATE creator_tasks SET stage='video',revision=revision+1,updated_at=? WHERE id=?", (stamp,payload.task_id))
        secret = cipher(settings.data_dir).decrypt(config["encrypted_key"].encode()).decode()
    return identity, {"key": secret, "config": dict(config), "path": source_path, "rules": rules}


class ProviderFailure(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


def execute_run(settings: Settings, run_id: str, frozen: dict, *, transport=None):
    """单实例线程执行，持久化终态；超时不重试，重启不重新提交可能已计费请求。"""
    config = frozen["config"]
    with connection(settings.database_path) as db:
        changed = db.execute("UPDATE evidence_runs SET status='processing',updated_at=? WHERE id=? AND status='queued'",
                             (now(), run_id)).rowcount
    if not changed:
        return
    try:
        run = get_run(settings, run_id)
        with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=False, transport=transport) as client:
            model_headers = {"Authorization": "Bearer " + frozen["key"]}
            def complete(messages, tools):
                call_id=uuid4().hex;started=time.monotonic()
                stage=tools[0]['function']['name'].removeprefix('submit_') if tools and len(tools)==1 and tools[0]['function']['name'].startswith('submit_') else 'legacy'
                def record_call(status,**details):
                    with connection(settings.database_path) as db:
                        stored=db.execute('SELECT result_json FROM evidence_runs WHERE id=?',(run_id,)).fetchone()
                        trace=json.loads(stored['result_json'] or '{}')
                        calls=trace.setdefault('stageCalls',[])
                        entry=next((c for c in calls if c['id']==call_id),None)
                        if entry is None:
                            entry={'id':call_id,'stage':stage,'startedAt':now()};calls.append(entry)
                        entry.update(status=status,durationMs=round((time.monotonic()-started)*1000),**details)
                        db.execute("UPDATE evidence_runs SET result_json=?,updated_at=? WHERE id=? AND status='processing'",(json.dumps(trace,ensure_ascii=False),now(),run_id))
                record_call('processing')
                # 判定类调用必须显式钉温度：不发的话按服务商默认采样，同一视频同一规则多次结果会漂移。
                body = {"model": config["model"], "messages": messages, "stream": False, "reasoning_effort": "none",
                        "temperature": float(os.getenv("REVIEW_VIDEO_TEMPERATURE", "0"))}
                if tools:
                    body["tools"] = tools
                    # 批量模式：允许模型在首轮一条消息里并发提交全部检查调用。
                    body["parallel_tool_calls"] = True
                    if len(tools)==1 and tools[0]['function']['name'] in ('submit_localization','submit_refinement'):
                        body['tool_choice']={'type':'function','function':{'name':tools[0]['function']['name']}}
                        body['parallel_tool_calls']=False
                if not tools and run.kind == "video":
                    body["tool_choice"] = "none"
                try:
                    response = client.post(config["endpoint"].rstrip("/") + "/chat/completions",
                        headers=model_headers, json=body)
                except httpx.HTTPError:
                    record_call('submission_unknown')
                    raise ProviderFailure("PROVIDER_SUBMISSION_UNKNOWN", "连接中断或超时，是否计费未知；未自动重试") from None
                if response.status_code != 200:
                    record_call('failed',httpStatus=response.status_code,rawResponse=response.text.replace(frozen['key'],'[REDACTED]')[:200_000])
                    if response.status_code == 413:
                        raise ProviderFailure('PROVIDER_HTTP_413', '模型服务返回 HTTP 413：请求体过大；平台未按视频大小拦截，需压缩副本或使用服务商支持的其他传输方式，未自动重试')
                    raise ProviderFailure(f"PROVIDER_HTTP_{response.status_code}",
                                          f"服务商返回 HTTP {response.status_code}；检查 Key、地域及模型权限，未自动重试")
                try:
                    result = response.json()
                    if not isinstance(result,dict):raise ValueError('模型服务未返回对象')
                except ValueError:
                    record_call('failed',errorCode='INVALID_PROVIDER_RESPONSE',rawResponse=response.text.replace(frozen['key'],'[REDACTED]')[:200_000])
                    raise ProviderFailure("INVALID_PROVIDER_RESPONSE", "服务商响应不可解析，未自动重试") from None
                record_call('returned',requestId=result.get('id'),usage=result.get('usage'))
                # 每一轮都记录脱敏计费线索，即使随后工具/JSON校验失败也可核对用量。
                with connection(settings.database_path) as db:
                    previous = db.execute("SELECT result_json FROM evidence_runs WHERE id=?", (run_id,)).fetchone()
                    trace = json.loads(previous["result_json"]) if previous["result_json"] else {}
                    trace.setdefault("providerRequestIds", [])
                    trace.setdefault("usage", [])
                    if result.get("id"):
                        trace["providerRequestIds"].append(result["id"])
                    if result.get("usage"):
                        trace["usage"].append(result["usage"])
                    choices = result.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        message = choices[0].get("message") or {}
                        diagnostic = {"finishReason": choices[0].get("finish_reason"),
                                      "content": message.get("content"), "toolCalls": message.get("tool_calls")}
                        # Retain returned report/tool requests only, never sent video, headers or reasoning.
                        encoded = json.dumps(diagnostic, ensure_ascii=False).replace(frozen["key"], "[REDACTED]")
                        trace.setdefault("modelResponses", []).append({"requestId": result.get("id"), "response": encoded[:200_000]})
                    db.execute("UPDATE evidence_runs SET result_json=?,updated_at=? WHERE id=? AND status='processing'",
                               (json.dumps(trace, ensure_ascii=False), now(), run_id))
                return result
            if run.kind == "connection":
                reply = complete([{"role": "system", "content": run.snapshot["prompts"]["connection"]["content"]}, {"role":"user","content":"验证连接"}], [])
                result = {"text": str(reply["choices"][0]["message"].get("content") or "")[:1000],
                          "providerRequestIds": [reply.get("id")], "usage": [reply.get("usage")],
                          "coverageVerified": False, "note": "文本接口连通，不证明视频或音轨能力"}
            else:
                import imageio_ffmpeg
                from .tools import EvidenceSession
                from .agent import run_agent
                from .preprocessing import prepare_video
                output = settings.data_dir / "evidence" / run_id
                preparation, model_input = prepare_video(settings, run.file_id, run.snapshot.get('compressionPreset', 'balanced'), max_inline_bytes=run.snapshot.get('maxInlineBytes'))
                if not preparation['canSend']:
                    raise ProviderFailure('COMPRESSED_VIDEO_TOO_LARGE', '完整视频副本超过本次调用保存的大小上限，未发送模型；请调整设置或压缩档位')
                references=[]
                if run.snapshot.get('references'):
                    from app.modules.workflow.reference_images import get_reference
                    with connection(settings.database_path) as db:
                        for expected in run.snapshot['references']:
                            ref=get_reference(db,settings,run.snapshot['projectId'],expected['id'],True)
                            if ref['sha256']!=expected['sha256']:
                                raise ValueError('参考图与冻结快照不一致')
                            references.append(ref)
                session = EvidenceSession(video=frozen["path"], output_dir=output,
                                          ffmpeg=imageio_ffmpeg.get_ffmpeg_exe(), rules=frozen["rules"], script=run.snapshot.get("approvedScript", ""),references=references)
                if session.sha256 != run.snapshot["sourceSha256"]:
                    raise ProviderFailure("SOURCE_CHANGED", "源视频哈希变化，已阻止发送")
                # 音频证据通道（线路 A，第一阶段）：本地 ASR 转写一次，只作冻结证据，
                # 不参与本阶段任何组件 verdict；失败显式降级，不伪造、不阻断 VLM 审核。
                from .asr import enabled as asr_enabled, transcribe, unavailable
                if not asr_enabled():
                    track = unavailable("本地 ASR 已关闭（REVIEW_VIDEO_ASR=off）：口播以模型听到的原声音轨和画面字幕为准，不生成转写证据",
                                        source_sha256=session.sha256)
                    track['hasAudio'] = session.has_audio
                    session.audio_track = track
                elif session.has_audio:
                    session.audio_track = transcribe(session.video, imageio_ffmpeg.get_ffmpeg_exe(),
                                                     source_sha256=session.sha256, output_dir=output)
                else:
                    session.audio_track = unavailable("原视频不含音轨；口播与禁用词检查以人工核对为准",
                                                      source_sha256=session.sha256)
                session.save_manifest()
                remote_video = None
                if run.snapshot.get('videoTransport') == 'temporary_upload':
                    remote_video = upload_video(client, frozen['key'], config['model'], model_input)
                    model_headers['X-DashScope-OssResourceResolve'] = 'enable'
                from .component_runtime import components_enabled
                system_prompt = run.snapshot["prompts"]["video"]["content"]
                if not components_enabled():
                    # 关闭组件时提示词仍写着"必须调用 check_ 工具"，会让模型要么空等工具、要么编造组件结果。
                    system_prompt += ("\n\n【本次未启用检查组件】服务端这次没有下发 check_ 组件工具：所有规则（包括带 componentPlan 的时长、次数、露出类要求）都请直接依据画面与音轨判断。"
                                      "请写出你观察到的时间区间与估计值（例如「约 0:01–0:19 连续可见，累计约 18 秒，1 次独立出现」）并说明依据的画面特征；"
                                      "不要声称调用过组件，也不要引用「组件计算的结果」。确实无法判断时标 needs_confirmation 并说明卡在哪一步。"
                                      "如果本轮没有实际调用取证工具，evidence_ids 必须是空数组，不得编造证据 ID。")
                from langsmith import tracing_context
                with tracing_context(enabled=False):
                    if run.snapshot.get('pipelineVersion') == 'localized_refinement_v1':
                        from .localized import run_localized
                        def progress(pipeline):
                            with connection(settings.database_path) as db:
                                row=db.execute('SELECT result_json FROM evidence_runs WHERE id=?',(run_id,)).fetchone()
                                partial=json.loads(row['result_json'] or '{}')
                                partial.update(pipeline=pipeline, artifacts=session.artifacts)
                                db.execute("UPDATE evidence_runs SET result_json=?,updated_at=? WHERE id=? AND status='processing'",(json.dumps(partial,ensure_ascii=False),now(),run_id))
                        def upload_clip(path):
                            return upload_video(client, frozen['key'], config['model'], path)
                        result = run_localized(session,complete,video_input=model_input,video_url=remote_video,
                                               prompts=run.snapshot['prompts'],progress=progress,
                                               upload_clip=upload_clip if run.snapshot.get('videoTransport')=='temporary_upload' else None)
                    else:
                        result = run_agent(session, complete, video_input=model_input, max_inline_bytes=preparation["maxInlineBytes"], system_prompt=system_prompt, final_prompt=run.snapshot["prompts"]["final"]["content"], batch_observations=True, video_url=remote_video)
                result["transport"] = {"mode": run.snapshot.get("videoTransport", "inline"), "uploadedBytes": model_input.stat().st_size, "usesInlineBase64": remote_video is None}
                result["media"] = session.metadata()
                from app.modules.workflow.video_layers import active_rules
                result['ruleCoverage'] = {'enabledRuleCount':len(active_rules(run.snapshot['rules'])),
                                          'evaluatedRuleCount':len(session.rules),
                                          'unconfiguredRules':run.snapshot.get('unconfiguredRules',[])}
                result['audioTrack'] = session.audio_track
                result['preparation'] = preparation
        with connection(settings.database_path) as db:
            stored=db.execute('SELECT result_json FROM evidence_runs WHERE id=?',(run_id,)).fetchone()
            trace=json.loads(stored['result_json'] or '{}')
            result={**trace,**result}
            # Transport records every returned response before validation, including
            # failed refinement batches. Do not lose their billing diagnostics.
            for field in ('providerRequestIds','usage','modelResponses'):
                if field in trace:result[field]=trace[field]
            from app.core.timing import stamp_duration
            stamp_duration(db, 'evidence_runs', run_id, result)
            db.execute("UPDATE evidence_runs SET status='succeeded',result_json=?,updated_at=? WHERE id=? AND status='processing'",
                       (json.dumps(result, ensure_ascii=False), now(), run_id))
            if run.task_id:
                from app.modules.workflow.router import state
                workflow = state(db, run.task_id)
                if workflow['writable'] and workflow['latestVideoRunId'] == run_id:
                    db.execute("UPDATE creator_tasks SET stage=?,revision=revision+1,updated_at=? WHERE id=?",
                               (workflow['stage'],now(),run.task_id))
    except Exception as exc:
        from .agent import AnalysisFailure
        known = isinstance(exc, (ProviderFailure, AnalysisFailure, VideoUploadFailure))
        code = exc.code if known else "EVIDENCE_ANALYSIS_FAILED"
        message = exc.message if known else "取证或模型结果校验失败；未生成通过资格，未自动重试"
        with connection(settings.database_path) as db:
            stored = db.execute("SELECT result_json FROM evidence_runs WHERE id=?", (run_id,)).fetchone()
            partial = json.loads(stored["result_json"] or '{}')
            if 'session' in locals():
                partial['artifacts'] = session.artifacts
                partial['toolCalls'] = session.calls
                if session.audio_track is not None:
                    partial['audioTrack'] = session.audio_track
                partial['componentChecks']=[{k:v for k,v in value.items() if not k.startswith('_')} for value in session.component_results.values()]
            partial['coverageVerified'] = False
            from app.core.timing import stamp_duration
            stamp_duration(db, 'evidence_runs', run_id, partial)
            db.execute("UPDATE evidence_runs SET result_json=? WHERE id=?", (json.dumps(partial, ensure_ascii=False), run_id))
            db.execute("UPDATE evidence_runs SET status=?,error_code=?,error_message=?,updated_at=? WHERE id=? AND status='processing'",
                       ("interrupted" if code == "PROVIDER_SUBMISSION_UNKNOWN" else "failed", code, message, now(), run_id))
    finally:
        frozen["key"] = ""


def artifact_path(settings: Settings, run_id: str, artifact_id: str):
    run = get_run(settings, run_id)
    artifacts = (run.result or {}).get("artifacts", [])
    artifact = next((a for a in artifacts if a["id"] == artifact_id), None)
    if artifact is None:
        raise ApiError(404, "RESOURCE_NOT_FOUND", "证据不存在")
    root = (settings.data_dir / "evidence" / run_id).resolve()
    path = (root / artifact["filename"]).resolve()
    if path.parent != root or not path.is_file():
        raise ApiError(404, "RESOURCE_NOT_FOUND", "证据文件不可用")
    return path, "image/jpeg" if artifact["kind"] == "image" else "video/mp4"
