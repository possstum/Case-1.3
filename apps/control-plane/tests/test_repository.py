from pathlib import Path

from app.db import create_session_factory, init_db
from app.models import JobStatus
from app.repository import (
    create_job,
    get_job,
    get_job_artifacts,
    job_to_envelope,
    replace_job_artifacts,
    set_queue_job_id,
    update_job_state,
)
from app.schemas import GenerateSongRequest, JobResultLinks


def _session_factory(tmp_path: Path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'repository.db'}")
    init_db(factory)
    return factory


def test_repository_persists_job_defaults_and_queue_job_id(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path)
    payload = GenerateSongRequest(
        lyrics="la la la",
        tags=["indie"],
        duration=60,
    )

    with session_factory() as session:
        job = create_job(
            session,
            job_id="job-1",
            owner_id="owner-1",
            payload=payload,
            eta_seconds=180,
        )
        updated = set_queue_job_id(session, "job-1", "rq-job-1")

    assert job.artist == "Unknown Artist"
    assert job.title == "Untitled"
    assert updated is not None
    assert updated.queue_job_id == "rq-job-1"


def test_update_job_state_clamps_progress_and_replace_artifacts(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path)
    payload = GenerateSongRequest(
        lyrics="la la la",
        tags=["indie"],
        duration=60,
    )

    with session_factory() as session:
        create_job(
            session,
            job_id="job-2",
            owner_id="owner-2",
            payload=payload,
            eta_seconds=180,
        )
        update_job_state(
            session,
            job_id="job-2",
            status=JobStatus.RUNNING.value,
            progress=1.5,
            eta_seconds=10,
            result_url="https://files.example.com/job-2.mp3",
        )
        replace_job_artifacts(
            session,
            job_id="job-2",
            files={
                "vocals": "https://files.example.com/job-2/vocals.mp3",
                "mixed": "https://files.example.com/job-2/mixed.mp3",
            },
        )

    with session_factory() as session:
        job = get_job(session, "job-2")
        artifacts = get_job_artifacts(session, "job-2")

    assert job is not None
    assert job.progress == 1.0
    assert [artifact.kind for artifact in artifacts] == ["mixed", "vocals"]


def test_job_to_envelope_includes_error_and_result_links(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path)
    payload = GenerateSongRequest(
        lyrics="la la la",
        tags=["indie"],
        duration=60,
    )

    with session_factory() as session:
        job = create_job(
            session,
            job_id="job-3",
            owner_id="owner-3",
            payload=payload,
            eta_seconds=180,
        )
        job = update_job_state(
            session,
            job_id="job-3",
            status=JobStatus.FAILED.value,
            progress=-2.0,
            eta_seconds=0,
            error_message="boom",
        )

    assert job is not None
    envelope = job_to_envelope(
        job,
        result=JobResultLinks(mixed_url="https://files.example.com/job-3.mp3"),
    )

    assert envelope.progress == 0.0
    assert envelope.error is not None
    assert envelope.error.message == "boom"
    assert envelope.result is not None


def test_update_job_state_returns_none_for_missing_job(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path)

    with session_factory() as session:
        result = update_job_state(
            session,
            job_id="missing",
            status=JobStatus.FAILED.value,
        )

    assert result is None
