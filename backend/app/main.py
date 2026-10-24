from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from pydantic import TypeAdapter
from starlette.exceptions import HTTPException

from app.core.config import Settings
from app.core.contracts import Conclusion, ErrorEnvelope, JobStatus, ReviewScope, ReviewSnapshot, ReviewState, TaskStage, Verdict
from app.core.database import migrate
from app.core.errors import ApiError, api_error_handler, http_error_handler, unexpected_error_handler, validation_error_handler
from app.core.review_contracts import JobRecord, ReviewResult
from app.modules.brief_script.router import router as brief_router
from app.modules.foundation.files_router import router as files_router
from app.modules.foundation.router import router as foundation_router
from app.modules.foundation.tasks_router import router as tasks_router
from app.modules.human_review.router import router as human_router
from app.modules.video_model.router import router as video_router
from app.modules.video_model.evidence.console_router import router as evidence_router
from app.modules.workflow.router import router as workflow_router
from app.modules.workflow.standards import router as standards_router
from app.modules.workflow.reference_images import router as reference_images_router
from app.modules.workflow.project_brief import router as project_brief_router
from app.modules.workflow.brief_vision import router as brief_vision_router
from app.modules.workflow.brief_facts import router as brief_facts_router
from app.modules.workflow.fact_review import router as fact_review_router
from app.modules.workflow.page_review import router as page_review_router
from app.modules.workflow.brief_processing import router as brief_processing_router
from app.modules.workflow.brief_import import router as brief_import_router
from app.modules.workflow.script_layout import router as script_layout_router
from app.modules.workflow.image_posts import router as image_posts_router
from app.modules.workflow.decomposition_settings import router as decomposition_settings_router
from app.modules.workflow.discussions import router as discussions_router
from app.modules.workflow.submission_records import router as submission_records_router


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        migrate(config.database_path)
        from concurrent.futures import ThreadPoolExecutor
        from app.core.database import connection
        from app.modules.foundation.service import now
        with connection(config.database_path) as db:
            from app.modules.workflow.standards import adopt_legacy_project_standards
            adopt_legacy_project_standards(db)
            from app.modules.workflow.project_brief import adopt_confirmed_project_briefs
            adopt_confirmed_project_briefs(db)
            from .modules.video_model.evidence.localized import interrupt_pipeline
            import json
            for pending_run in db.execute("SELECT id,result_json FROM evidence_runs WHERE status IN ('queued','processing')").fetchall():
                partial=json.loads(pending_run['result_json'] or '{}')
                if partial.get('pipeline'):
                    partial['pipeline']=interrupt_pipeline(partial['pipeline'])
                    for call in partial.get('stageCalls',[]):
                        if call['status']=='processing':call['status']='submission_unknown'
                    db.execute('UPDATE evidence_runs SET result_json=? WHERE id=?',(json.dumps(partial,ensure_ascii=False),pending_run['id']))
            db.execute("UPDATE evidence_runs SET status='interrupted',error_code='SERVER_RESTARTED',"
                       "error_message='服务已重启；先核对供应商用量，不会自动重新提交收费请求',updated_at=? "
                       "WHERE status IN ('queued','processing')", (now(),))
            db.execute("UPDATE workflow_script_reviews SET status='interrupted',error_code='SERVER_RESTARTED',"
                       "error_message='服务重启，调用状态待核实；不会自动重发',updated_at=? WHERE status='processing'", (now(),))
            db.execute("UPDATE image_post_reviews SET status='interrupted',error_code='SERVER_RESTARTED',"
                       "error_message='服务重启，图文调用状态待核实；不会自动重发',updated_at=? WHERE status='processing'", (now(),))
            db.execute("UPDATE standard_recommendations SET status='interrupted',error_message='服务重启；调用状态待核实，不会自动重发',updated_at=? WHERE status='processing'", (now(),))
            db.execute("UPDATE brief_visual_runs SET status='interrupted',error_code='SERVER_RESTARTED',error_message='服务重启；调用状态待核实，不会自动重发',updated_at=? WHERE status IN ('queued','processing')",(now(),))
            db.execute("UPDATE brief_processing_runs SET status='interrupted',error_code='SERVER_RESTARTED',error_message='服务重启，自动处理已暂停；核对用量后手动继续',updated_at=? WHERE status IN ('queued','vision','facts')",(now(),))
            db.execute("UPDATE brief_facts_runs SET status='interrupted',error_message='服务重启，事实卡调用状态待核实；不会自动重发',updated_at=? WHERE status='processing'",(now(),))
            db.execute("UPDATE brief_project_imports SET status='interrupted',error_code='SERVER_RESTARTED',error_message='服务重启，项目识别已暂停；不会自动重发收费调用',updated_at=? WHERE status IN ('queued','processing')", (now(),))
        application.state.brief_import_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='brief-import')
        application.state.brief_processing_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='brief-processing')
        application.state.evidence_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evidence-test")
        application.state.brief_vision_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='brief-vision')
        try:
            yield
        finally:
            application.state.brief_import_executor.shutdown(wait=True)
            application.state.brief_processing_executor.shutdown(wait=True)
            application.state.evidence_executor.shutdown(wait=True)
            application.state.brief_vision_executor.shutdown(wait=True)

    application = FastAPI(title="达人营销智能审核平台", version="0.1.0", lifespan=lifespan,
                          description="四人协作骨架；未接入业务接口明确返回 501，不产生审核结果。")
    application.state.settings = config

    @application.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(HTTPException, http_error_handler)
    application.add_exception_handler(Exception, unexpected_error_handler)

    @application.get("/health", tags=["health"])
    def health():
        return {"status": "ok", "version": "0.1.0", "stage": "foundation"}

    errors = {status: {"model": ErrorEnvelope} for status in [400, 404, 409, 501]}
    for router in [foundation_router, files_router, tasks_router, brief_router, evidence_router, workflow_router, standards_router, decomposition_settings_router, reference_images_router, project_brief_router, brief_vision_router, brief_facts_router, fact_review_router, brief_processing_router, brief_import_router, script_layout_router, image_posts_router, video_router, human_router]:
        application.include_router(router, prefix="/api/v1", responses=errors)
    application.include_router(page_review_router, prefix="/api/v1", responses=errors)
    application.include_router(discussions_router, prefix="/api/v1", responses=errors)
    application.include_router(submission_records_router, prefix="/api/v1", responses=errors)

    def openapi():
        if application.openapi_schema is None:
            schema = get_openapi(title=application.title, version=application.version,
                                 description=application.description, routes=application.routes)
            # 未实施业务接口也可共享状态类型，避免四组手工复制枚举。
            catalog = TypeAdapter(tuple[Conclusion, JobStatus, ReviewScope, ReviewSnapshot, ReviewState, TaskStage, Verdict, JobRecord, ReviewResult])
            definitions = catalog.json_schema(ref_template="#/components/schemas/{model}").get("$defs", {})
            schema.setdefault("components", {}).setdefault("schemas", {}).update(definitions)
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = openapi
    return application


app = create_app()
