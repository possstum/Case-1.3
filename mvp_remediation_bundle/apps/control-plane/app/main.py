from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import asyncio
import math

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from starlette.exceptions import WebSocketException
from starlette.websockets import WebSocketDisconnect

from .config import Settings
from .db import create_session_factory, init_db
from .models import JobStatus
from .queueing import QueueBackend, build_queue_backend
from .rate_limit import RateLimitDecision, TokenBucketRateLimiter
from .repository import (
    create_job,
    get_job,
    get_job_artifacts,
    job_to_envelope,
    job_to_ws_envelope,
    set_queue_job_id,
)
from .schemas import (
    GenerateSongRequest,
    HealthResponse,
    JobEnvelope,
    JobResultLinks,
    JobResultResponse,
    ModelInfo,
    ModelsResponse,
    RateLimitResponse,
)
from .security import (
    AuthContext,
    authenticate_websocket,
    build_signed_url,
    get_auth_context,
)


INDEX_HTML_PATH = Path(__file__).resolve().parent / "static" / "index.html"


def _build_signed_result_files(
    settings: Settings,
    *,
    raw_result_url: str | None,
    artifacts,
) -> tuple[dict[str, str], object | None]:
    signed_files: dict[str, str] = {}
    expires_at = None

    for artifact in artifacts:
        signed_file_url, artifact_expires_at = build_signed_url(
            artifact.url,
            secret=settings.signed_url_secret,
            ttl_seconds=settings.signed_url_ttl_seconds,
        )
        signed_files[artifact.kind] = signed_file_url
        expires_at = expires_at or artifact_expires_at

    if signed_files.get("mixed") is None:
        if not raw_result_url:
            return signed_files, expires_at
        signed_url, expires_at = build_signed_url(
            raw_result_url,
            secret=settings.signed_url_secret,
            ttl_seconds=settings.signed_url_ttl_seconds,
        )
        signed_files["mixed"] = signed_url

    return signed_files, expires_at


def _resolve_demo_artifact_path(settings: Settings, job_id: str, filename: str) -> Path:
    artifact_root = Path(settings.demo_artifacts_dir)
    if not artifact_root.is_absolute():
        artifact_root = Path.cwd() / artifact_root
    requested = (artifact_root / job_id / filename).resolve()
    root_resolved = artifact_root.resolve()
    if root_resolved not in requested.parents:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact not found",
        )
    return requested


def create_app(
    settings: Settings | None = None,
    *,
    queue_backend: QueueBackend | None = None,
    rate_limiter: TokenBucketRateLimiter | None = None,
) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_sqlite_directory()
    session_factory = create_session_factory(settings.database_url)
    queue_backend = queue_backend or build_queue_backend(settings)
    rate_limiter = rate_limiter or TokenBucketRateLimiter(
        capacity=settings.rate_limit_capacity,
        refill_per_second=settings.rate_limit_refill_per_second,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.session_factory = session_factory
        app.state.queue_backend = queue_backend
        app.state.rate_limiter = rate_limiter
        init_db(session_factory)
        yield
        session_factory.kw["bind"].dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        lifespan=lifespan,
    )

    def rate_limit_guard(
        request: Request,
        auth: AuthContext = Depends(get_auth_context),
    ) -> RateLimitDecision:
        limiter: TokenBucketRateLimiter = request.app.state.rate_limiter
        decision = limiter.consume(auth.api_key_hash)
        if not decision.allowed:
            retry_after_seconds = decision.retry_after_seconds
            if not math.isfinite(retry_after_seconds) or retry_after_seconds <= 0:
                retry_after_seconds = 1.0
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(int(retry_after_seconds))},
            )
        return decision

    def load_owned_job(request: Request, job_id: str, owner_id: str):
        with request.app.state.session_factory() as session:
            job = get_job(session, job_id)
            if job is None or job.owner_id != owner_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Job not found",
                )
            return job

    @app.get("/", include_in_schema=False)
    @app.get("/ui", include_in_schema=False)
    def serve_ui() -> FileResponse:
        return FileResponse(INDEX_HTML_PATH)

    @app.get("/healthz", response_model=HealthResponse)
    def healthz(request: Request) -> JSONResponse:
        db_status = "ok"
        redis_status = "ok"

        try:
            with request.app.state.session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            db_status = "error"

        try:
            if not request.app.state.queue_backend.healthcheck():
                redis_status = "error"
        except Exception:
            redis_status = "error"

        overall_status = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
        code = status.HTTP_200_OK if overall_status == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(
            status_code=code,
            content=HealthResponse(
                status=overall_status,
                database=db_status,
                redis=redis_status,
            ).model_dump(mode="json"),
        )

    @app.get("/artifacts/{job_id}/{filename}", include_in_schema=False)
    def serve_demo_artifact(job_id: str, filename: str, request: Request) -> FileResponse:
        artifact_path = _resolve_demo_artifact_path(
            request.app.state.settings,
            job_id,
            filename,
        )
        if not artifact_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Artifact not found",
            )
        return FileResponse(artifact_path, media_type="audio/wav")

    @app.get(
        "/v1/models",
        response_model=ModelsResponse,
        dependencies=[Depends(rate_limit_guard)],
    )
    def list_models(
        request: Request,
        auth: AuthContext = Depends(get_auth_context),
    ) -> ModelsResponse:
        _ = auth
        models = [
            ModelInfo(
                id=model_name,
                provider="gpu-worker",
                status="available",
                metadata={"queue_name": request.app.state.settings.queue_name},
            )
            for model_name in request.app.state.settings.available_models
        ]
        return ModelsResponse(models=models)

    @app.get("/v1/rate-limits", response_model=RateLimitResponse)
    def get_rate_limits(
        request: Request,
        auth: AuthContext = Depends(get_auth_context),
    ) -> RateLimitResponse:
        limiter: TokenBucketRateLimiter = request.app.state.rate_limiter
        decision = limiter.peek(auth.api_key_hash)
        return RateLimitResponse(
            key_fingerprint=auth.api_key_fingerprint,
            capacity=decision.capacity,
            remaining_tokens=round(decision.remaining_tokens, 3),
            refill_per_second=decision.refill_per_second,
            retry_after_seconds=decision.retry_after_seconds,
        )

    @app.post(
        "/v1/jobs",
        response_model=JobEnvelope,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(rate_limit_guard)],
    )
    def create_generation_job(
        payload: GenerateSongRequest,
        request: Request,
        auth: AuthContext = Depends(get_auth_context),
    ) -> JobEnvelope:
        settings: Settings = request.app.state.settings
        if payload.model_name not in settings.available_models:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported model '{payload.model_name}'",
            )

        job_id = str(uuid4())
        with request.app.state.session_factory() as session:
            job = create_job(
                session,
                job_id=job_id,
                owner_id=auth.api_key_hash,
                payload=payload,
                eta_seconds=settings.default_eta_seconds,
            )
            try:
                queue_job_id = request.app.state.queue_backend.enqueue(
                    job_id,
                    payload.model_dump(),
                )
            except Exception as exc:
                from .repository import update_job_state

                update_job_state(
                    session,
                    job_id=job_id,
                    status=JobStatus.FAILED.value,
                    progress=1.0,
                    eta_seconds=0,
                    error_message=f"Queue enqueue failed: {exc}",
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Unable to enqueue job",
                ) from exc
            job = set_queue_job_id(session, job_id, queue_job_id) or job
            return job_to_envelope(job)

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=JobEnvelope,
        dependencies=[Depends(rate_limit_guard)],
    )
    def fetch_job(
        job_id: str,
        request: Request,
        auth: AuthContext = Depends(get_auth_context),
    ) -> JobEnvelope:
        job = load_owned_job(request, job_id, auth.api_key_hash)
        result = None

        if job.status == JobStatus.COMPLETED.value:
            with request.app.state.session_factory() as session:
                artifacts = get_job_artifacts(session, job_id)
            signed_files, expires_at = _build_signed_result_files(
                request.app.state.settings,
                raw_result_url=job.result_url,
                artifacts=artifacts,
            )
            mixed_url = signed_files.get("mixed")
            if mixed_url:
                result = JobResultLinks(
                    mixed_url=mixed_url,
                    vocals_url=signed_files.get("vocals"),
                    instrumental_url=signed_files.get("instrumental"),
                    expires_at=expires_at,
                )

        return job_to_envelope(job, result=result)

    @app.get(
        "/v1/jobs/{job_id}/result",
        response_model=JobResultResponse,
        dependencies=[Depends(rate_limit_guard)],
    )
    def fetch_job_result(
        job_id: str,
        request: Request,
        redirect: bool = Query(default=True),
        auth: AuthContext = Depends(get_auth_context),
    ):
        with request.app.state.session_factory() as session:
            job = get_job(session, job_id)
            if job is None or job.owner_id != auth.api_key_hash:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Job not found",
                )
            artifacts = get_job_artifacts(session, job_id)

        if job.status != JobStatus.COMPLETED.value or not job.result_url:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Result is not ready",
            )

        signed_files, expires_at = _build_signed_result_files(
            request.app.state.settings,
            raw_result_url=job.result_url,
            artifacts=artifacts,
        )
        signed_url = signed_files.get("mixed")

        assert signed_url is not None
        assert expires_at is not None
        if redirect:
            return RedirectResponse(url=signed_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return JobResultResponse(
            job_id=job.id,
            signed_url=signed_url,
            expires_at=expires_at,
            files=signed_files,
        )

    @app.websocket("/v1/ws")
    async def websocket_progress(websocket: WebSocket) -> None:
        settings: Settings = websocket.app.state.settings
        try:
            auth = authenticate_websocket(websocket, settings)
        except HTTPException as exc:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION) from exc

        job_id = websocket.query_params.get("job_id")
        if not job_id:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

        await websocket.accept()
        last_payload: dict[str, object] | None = None

        while True:
            with websocket.app.state.session_factory() as session:
                job = get_job(session, job_id)
                if job is None or job.owner_id != auth.api_key_hash:
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                payload = job_to_ws_envelope(job).model_dump(mode="json")

            if payload != last_payload:
                try:
                    await websocket.send_json(payload)
                except WebSocketDisconnect:
                    return
                last_payload = payload

            if job.status in {
                JobStatus.COMPLETED.value,
                JobStatus.FAILED.value,
            }:
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return

            await asyncio.sleep(settings.websocket_poll_interval_seconds)

    return app


app = create_app()
