"""Authenticated document-analysis run API (v1)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db import get_db
from app.dependencies import (
    Identity,
    get_current_user,
    get_current_user_sse,
    get_ontology_engine,
)
from app.schemas.document_analysis import (
    ApiErrorResponse,
    CreateRunRequest,
    CreateRunResponse,
    CreateTemplateRunRequest,
    DeleteRunRequest,
    DocumentAnalysisRunListResponse,
    DocumentAnalysisRunResponse,
    GraphArtifactResponse,
    GraphProjection,
    MetadataArtifactResponse,
    RunControlRequest,
    RunControlResponse,
    SourceArtifactResponse,
    SourceQuery,
    SseEvent,
    TemplateRunResponse,
)
from app.services.document_analysis.application import (
    DocumentAnalysisApplication,
    DocumentAnalysisError,
    weak_etag,
)
from app.services.document_analysis.execution import (
    dispatch_run,
    notify_document_analysis_dispatcher,
)
from app.services.document_analysis.template_runs import TemplateDocumentRuns


class DocumentAnalysisRoute(APIRoute):
    """Keep framework/dependency failures inside the public v1 error contract."""

    def get_route_handler(self):
        original_route_handler = super().get_route_handler()

        async def contract_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except RequestValidationError:
                return _validation_error()
            except DocumentAnalysisError as exc:
                return _error(exc)
            except StarletteHTTPException as exc:
                return _http_exception_error(exc)
            except RuntimeError as exc:
                # The shared ontology dependency currently signals its startup
                # state with this exact error.  Do not disguise unrelated bugs.
                if str(exc) != "Ontology not loaded":
                    raise
                return _error(
                    DocumentAnalysisError(
                        "ONTOLOGY_UNAVAILABLE",
                        "当前本体不可用，未创建分析运行",
                        status_code=503,
                        retryable=True,
                    )
                )

        return contract_route_handler


ERROR_RESPONSES = {
    status_code: {"model": ApiErrorResponse}
    for status_code in (400, 401, 403, 404, 409, 410, 413, 415, 422, 503)
}

router = APIRouter(route_class=DocumentAnalysisRoute, responses=ERROR_RESPONSES)
_CREATE_RUN_FORM_FIELDS = {"file", "root_class_iri", "request_key", "metadata_mode"}


def _application(db: Session, engine: object | None = None) -> DocumentAnalysisApplication:
    return DocumentAnalysisApplication(db, ontology_engine=engine or object())


def _error(exc: DocumentAnalysisError) -> JSONResponse:
    payload = ApiErrorResponse(
        error={
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
            "current_revision": exc.current_revision,
        }
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))


def _validation_error(message: str = "请求字段非法") -> JSONResponse:
    return _error(DocumentAnalysisError("INVALID_REQUEST", message, status_code=400))


def _http_exception_error(exc: StarletteHTTPException) -> JSONResponse:
    message = str(exc.detail)
    if exc.status_code == 401 or (
        exc.status_code == 403 and ("缺少身份" in message or "未认证" in message)
    ):
        return _error(
            DocumentAnalysisError(
                "UNAUTHENTICATED",
                message,
                status_code=401,
            )
        )
    if exc.status_code == 403:
        return _error(
            DocumentAnalysisError(
                "ROLE_FORBIDDEN",
                message,
                status_code=403,
            )
        )
    return _error(
        DocumentAnalysisError(
            "INVALID_REQUEST",
            message,
            status_code=exc.status_code,
        )
    )


def unauthenticated_error(message: str = "未认证：请先登录") -> JSONResponse:
    """Return the route contract from the API-wide auth middleware."""

    return _error(
        DocumentAnalysisError(
            "UNAUTHENTICATED",
            message,
            status_code=401,
        )
    )


def _json_model(model, *, status_code: int = 200, headers: dict[str, str] | None = None):
    return JSONResponse(
        status_code=status_code,
        content=model.model_dump(mode="json"),
        headers=headers,
    )


def _if_not_modified(if_none_match: str | None, etag: str) -> Response | None:
    if if_none_match and etag in {value.strip() for value in if_none_match.split(",")}:
        return Response(status_code=304, headers={"ETag": etag})
    return None


def _wake_dispatcher_or_fallback(
    background_tasks: BackgroundTasks,
    recognition_run_id: UUID,
    *,
    bind: Engine,
) -> None:
    """Wake the lifespan dispatcher; retain a lifespan-less test fallback."""

    if notify_document_analysis_dispatcher():
        return
    # TestClient users and embedded ASGI callers may intentionally omit lifespan.
    # The production application always takes the notification-only branch.
    background_tasks.add_task(dispatch_run, recognition_run_id, bind=bind)


@router.get("/templates/{template_id}/sources/{job_id}/runs", response_model=TemplateRunResponse)
def get_template_document_run(
    template_id: UUID,
    job_id: UUID,
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
    engine: object = Depends(get_ontology_engine),
):
    application = _application(db, engine)
    run = TemplateDocumentRuns(application).latest(identity.username, template_id, job_id)
    return {"run": application.status_response(run, role=identity.role) if run else None}


@router.post("/templates/{template_id}/sources/{job_id}/runs",
             response_model=CreateRunResponse, status_code=202)
async def create_template_document_run(
    template_id: UUID,
    job_id: UUID,
    body: CreateTemplateRunRequest,
    background_tasks: BackgroundTasks,
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
    engine: object = Depends(get_ontology_engine),
):
    if identity.role != "senior_analyst":
        raise DocumentAnalysisError("ROLE_FORBIDDEN", "当前角色无运行写权限", status_code=403)
    application = _application(db, engine)
    run, created = await TemplateDocumentRuns(application).create(
        identity.username, template_id, job_id, body.request_key)
    if created:
        _wake_dispatcher_or_fallback(background_tasks, run.recognition_run_id, bind=db.get_bind())
    return application.create_response(run, idempotent_replay=not created)


@router.post("/runs", response_model=CreateRunResponse, status_code=202)
async def create_document_analysis_run(
    request: Request,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(...)],
    root_class_iri: Annotated[str, Form(...)],
    request_key: Annotated[str, Form(...)],
    metadata_mode: Annotated[str, Form()] = "generate_summary",
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
    engine: object = Depends(get_ontology_engine),
):
    unexpected = sorted(set((await request.form()).keys()) - _CREATE_RUN_FORM_FIELDS)
    if unexpected:
        await file.close()
        return _validation_error(f"请求包含未知字段：{', '.join(unexpected)}")
    if identity.role != "senior_analyst":
        return _error(
            DocumentAnalysisError("ROLE_FORBIDDEN", "当前角色无运行写权限", status_code=403)
        )
    try:
        form = CreateRunRequest(
            root_class_iri=root_class_iri,
            request_key=request_key,
            metadata_mode=metadata_mode,
        )
    except ValidationError as exc:
        await file.close()
        if any(error["loc"] == ("root_class_iri",) for error in exc.errors()):
            return _error(
                DocumentAnalysisError(
                    "INVALID_ROOT_CLASS",
                    "root_class_iri 必须是完整 IRI",
                    status_code=422,
                )
            )
        return _validation_error()
    app = _application(db, engine)
    try:
        run, created = await app.create_run(
            owner_id=identity.username,
            file=file,
            root_class_iri=form.root_class_iri,
            request_key=form.request_key,
            metadata_mode=form.metadata_mode,
        )
        response = CreateRunResponse.model_validate(
            app.create_response(run, idempotent_replay=not created)
        )
    except DocumentAnalysisError as exc:
        return _error(exc)
    if created:
        _wake_dispatcher_or_fallback(
            background_tasks,
            run.recognition_run_id,
            bind=db.get_bind(),
        )
    return response


@router.get("/runs", response_model=DocumentAnalysisRunListResponse)
def list_document_analysis_runs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = DocumentAnalysisRunListResponse.model_validate(
        _application(db).list_runs(identity.username, limit=limit, offset=offset)
    )
    return _json_model(response, headers={"Cache-Control": "no-store"})


@router.get("/runs/{recognition_run_id}", response_model=DocumentAnalysisRunResponse)
def get_document_analysis_run(
    recognition_run_id: UUID,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = _application(db)
    try:
        run = app.get_run(recognition_run_id, identity.username)
        etag = weak_etag(run)
        cached = _if_not_modified(if_none_match, etag)
        if cached is not None:
            return cached
        response = DocumentAnalysisRunResponse.model_validate(
            app.status_response(run, role=identity.role)
        )
        return _json_model(response, headers={"ETag": etag})
    except DocumentAnalysisError as exc:
        return _error(exc)


@router.get("/runs/{recognition_run_id}/metadata", response_model=MetadataArtifactResponse)
def get_document_analysis_metadata(
    recognition_run_id: UUID,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = _application(db)
    try:
        run = app.get_run(recognition_run_id, identity.username)
        etag = weak_etag(run)
        cached = _if_not_modified(if_none_match, etag)
        if cached is not None:
            return cached
        response = MetadataArtifactResponse.model_validate(app.metadata_response(run))
        return _json_model(response, headers={"ETag": etag})
    except DocumentAnalysisError as exc:
        return _error(exc)


@router.get("/runs/{recognition_run_id}/graph", response_model=GraphArtifactResponse)
def get_document_analysis_graph(
    recognition_run_id: UUID,
    projection: GraphProjection = Query(default="effective_affirmed"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = _application(db)
    try:
        run = app.get_run(recognition_run_id, identity.username)
        etag = weak_etag(run)
        cached = _if_not_modified(if_none_match, etag)
        if cached is not None:
            return cached
        response = GraphArtifactResponse.model_validate(
            app.graph_response(run, projection=projection)
        )
        return _json_model(response, headers={"ETag": etag})
    except DocumentAnalysisError as exc:
        return _error(exc)


def _file_chunks(path, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            yield chunk


@router.get("/runs/{recognition_run_id}/source", response_model=SourceArtifactResponse)
def get_document_analysis_source(
    recognition_run_id: UUID,
    selection_ref: str | None = Query(default=None),
    source_format: str | None = Query(default=None, alias="format"),
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        query = SourceQuery(selection_ref=selection_ref, format=source_format)
    except ValidationError:
        return _validation_error("selection_ref 与 format 参数组合非法")
    app = _application(db)
    try:
        run = app.get_run(recognition_run_id, identity.username)
        if query.format == "original":
            path, media_type, filename, document_hash = app.original_source(run)
            encoded = quote(filename, safe="")
            return StreamingResponse(
                _file_chunks(path),
                media_type=media_type,
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="document{path.suffix}"; '
                        f"filename*=UTF-8''{encoded}"
                    ),
                    "X-Document-Hash": document_hash,
                    "Cache-Control": "private, no-store",
                },
            )
        response = SourceArtifactResponse.model_validate(
            app.source_response(run, selection_ref=query.selection_ref)
        )
        return _json_model(response, headers={"Cache-Control": "private, no-store"})
    except DocumentAnalysisError as exc:
        return _error(exc)


def _read_sse_batch(
    bind: Engine,
    recognition_run_id: UUID,
    owner_id: str,
    cursor: int,
) -> tuple[list[tuple[int, str, dict]], str]:
    with Session(bind) as reader:
        app = _application(reader)
        run = app.get_run(recognition_run_id, owner_id)
        return app.events_response(run, after_sequence=cursor), run.execution_status


@router.get("/runs/{recognition_run_id}/events")
async def stream_document_analysis_events(
    recognition_run_id: UUID,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    identity: Identity = Depends(get_current_user_sse),
    db: Session = Depends(get_db),
):
    try:
        cursor = int(last_event_id or 0)
        if cursor < 0:
            raise ValueError
    except ValueError:
        return _validation_error("Last-Event-ID 必须是非负事件序号")
    app = _application(db)
    try:
        app.get_run(recognition_run_id, identity.username)
    except DocumentAnalysisError as exc:
        return _error(exc)
    bind = db.get_bind()
    db.rollback()

    async def frames():
        nonlocal cursor
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.document_analysis_sse_window_seconds
        terminal = {"finished", "failed", "blocked_dependency", "paused", "cancelled"}
        while loop.time() < deadline:
            if await request.is_disconnected():
                return
            try:
                events, execution_status = await asyncio.to_thread(
                    _read_sse_batch,
                    bind,
                    recognition_run_id,
                    identity.username,
                    cursor,
                )
            except DocumentAnalysisError:
                return
            for sequence, event_type, data in events:
                event = SseEvent(id=sequence, event=event_type, data=data)
                yield (
                    f"id: {event.id}\n"
                    f"event: {event.event}\n"
                    f"data: {event.data.model_dump_json()}\n\n"
                )
                cursor = sequence
            if execution_status in terminal and not events:
                return
            if not events:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _control_response(
    *,
    action: str,
    recognition_run_id: UUID,
    request: RunControlRequest,
    identity: Identity,
    db: Session,
    background_tasks: BackgroundTasks,
):
    app = _application(db)
    try:
        run = app.get_run(recognition_run_id, identity.username)
        updated, replay = app.control(
            run,
            action=action,
            expected_revision=request.expected_revision,
            request_key=request.request_key,
            reason=request.reason,
            role=identity.role,
        )
        if action == "resume" and not replay:
            _wake_dispatcher_or_fallback(
                background_tasks,
                updated.recognition_run_id,
                bind=db.get_bind(),
            )
        response = RunControlResponse.model_validate(
            app.control_response(
                updated,
                action=action,
                role=identity.role,
                request_key=request.request_key,
            )
        )
        return _json_model(response, status_code=202)
    except DocumentAnalysisError as exc:
        return _error(exc)


@router.post("/runs/{recognition_run_id}/pause", response_model=RunControlResponse)
def pause_document_analysis_run(
    recognition_run_id: UUID,
    request: RunControlRequest,
    background_tasks: BackgroundTasks,
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _control_response(
        action="pause",
        recognition_run_id=recognition_run_id,
        request=request,
        identity=identity,
        db=db,
        background_tasks=background_tasks,
    )


@router.post("/runs/{recognition_run_id}/resume", response_model=RunControlResponse)
def resume_document_analysis_run(
    recognition_run_id: UUID,
    request: RunControlRequest,
    background_tasks: BackgroundTasks,
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _control_response(
        action="resume",
        recognition_run_id=recognition_run_id,
        request=request,
        identity=identity,
        db=db,
        background_tasks=background_tasks,
    )


@router.post("/runs/{recognition_run_id}/cancel", response_model=RunControlResponse)
def cancel_document_analysis_run(
    recognition_run_id: UUID,
    request: RunControlRequest,
    background_tasks: BackgroundTasks,
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _control_response(
        action="cancel",
        recognition_run_id=recognition_run_id,
        request=request,
        identity=identity,
        db=db,
        background_tasks=background_tasks,
    )


@router.post("/runs/{recognition_run_id}/ranking-budget/{mode}", response_model=RunControlResponse)
def set_document_analysis_ranking_budget(
    recognition_run_id: UUID,
    mode: Literal["enable", "disable"],
    request: RunControlRequest,
    background_tasks: BackgroundTasks,
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _control_response(
        action=f"ranking_budget_{mode}",
        recognition_run_id=recognition_run_id,
        request=request,
        identity=identity,
        db=db,
        background_tasks=background_tasks,
    )


@router.delete("/runs/{recognition_run_id}", response_model=RunControlResponse)
def delete_document_analysis_run(
    recognition_run_id: UUID,
    background_tasks: BackgroundTasks,
    expected_revision: int = Query(ge=0),
    request_key: str = Query(min_length=1, max_length=200),
    identity: Identity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        request = DeleteRunRequest(
            expected_revision=expected_revision,
            request_key=request_key,
        )
    except ValidationError:
        return _validation_error()
    app = _application(db)
    try:
        updated, payload, replay = app.delete_control(
            recognition_run_id,
            identity.username,
            expected_revision=request.expected_revision,
            request_key=request.request_key,
            role=identity.role,
        )
        if not replay:
            if updated is None:  # Defensive: only a tombstone replay has no live run.
                raise DocumentAnalysisError(
                    "RUN_STATE_CONFLICT",
                    "删除操作缺少活动运行",
                    status_code=409,
                )
            # Imported lazily so API reads never initialise retention machinery.
            from app.services.document_analysis.retention import delete_run

            background_tasks.add_task(
                delete_run,
                updated.recognition_run_id,
                updated.owner_id,
                bind=db.get_bind(),
            )
        response = RunControlResponse.model_validate(payload)
        return _json_model(response, status_code=202)
    except DocumentAnalysisError as exc:
        return _error(exc)
