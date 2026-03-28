from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import Job, JobArtifact, JobStatus
from .schemas import (
    GenerateSongRequest,
    JobEnvelope,
    JobError,
    JobResultLinks,
    WebSocketJobEnvelope,
)


def create_job(
    session: Session,
    *,
    job_id: str,
    owner_id: str,
    payload: GenerateSongRequest,
    eta_seconds: int,
) -> Job:
    job = Job(
        id=job_id,
        owner_id=owner_id,
        model_name=payload.model_name,
        artist=payload.artist or "Unknown Artist",
        title=payload.title or "Untitled",
        lyrics=payload.lyrics,
        style_prompt=payload.style_prompt,
        status=JobStatus.QUEUED.value,
        progress=0.0,
        eta_seconds=eta_seconds,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def get_job(session: Session, job_id: str) -> Job | None:
    return session.get(Job, job_id)


def get_job_artifacts(session: Session, job_id: str) -> list[JobArtifact]:
    return (
        session.query(JobArtifact)
        .filter(JobArtifact.job_id == job_id)
        .order_by(JobArtifact.kind.asc())
        .all()
    )


def replace_job_artifacts(
    session: Session,
    *,
    job_id: str,
    files: dict[str, str],
) -> None:
    now = datetime.now(timezone.utc)
    session.query(JobArtifact).filter(JobArtifact.job_id == job_id).delete()
    for kind, url in files.items():
        session.add(
            JobArtifact(
                job_id=job_id,
                kind=kind,
                url=url,
                created_at=now,
                updated_at=now,
            )
        )
    session.commit()


def set_queue_job_id(session: Session, job_id: str, queue_job_id: str) -> Job | None:
    job = get_job(session, job_id)
    if job is None:
        return None
    job.queue_job_id = queue_job_id
    job.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(job)
    return job


def update_job_state(
    session: Session,
    *,
    job_id: str,
    status: str | None = None,
    progress: float | None = None,
    eta_seconds: int | None = None,
    result_url: str | None = None,
    error_message: str | None = None,
) -> Job | None:
    job = get_job(session, job_id)
    if job is None:
        return None
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = max(0.0, min(1.0, progress))
    if eta_seconds is not None:
        job.eta_seconds = eta_seconds
    if result_url is not None:
        job.result_url = result_url
    if error_message is not None:
        job.error_message = error_message
    job.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(job)
    return job


def job_to_envelope(
    job: Job,
    *,
    result: JobResultLinks | None = None,
) -> JobEnvelope:
    return JobEnvelope(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        eta_seconds=job.eta_seconds,
        model_name=job.model_name,
        created_at=job.created_at,
        updated_at=job.updated_at,
        error_message=job.error_message,
        result=result,
        error=JobError(message=job.error_message) if job.error_message else None,
    )


def job_to_ws_envelope(job: Job) -> WebSocketJobEnvelope:
    return WebSocketJobEnvelope(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        eta_seconds=job.eta_seconds,
    )
