from pathlib import Path

import pytest

from app.config import Settings
from app.db import create_session_factory, init_db
from app.models import JobStatus
from app.repository import create_job, get_job, get_job_artifacts
from app.schemas import GenerateSongRequest
from app.tasks import run_generation_job


def _create_job_record(tmp_path: Path):
    database_path = tmp_path / "tasks.db"
    session_factory = create_session_factory(f"sqlite:///{database_path}")
    init_db(session_factory)
    settings = Settings(
        database_url=f"sqlite:///{database_path}",
        queue_mode="inline",
        simulated_job_step_seconds=0.0,
        demo_artifacts_dir=str(tmp_path / "artifacts"),
    )

    with session_factory() as session:
        create_job(
            session,
            job_id="job-1",
            owner_id="owner-1",
            payload=GenerateSongRequest(
                lyrics="la la la",
                tags=["indie"],
                duration=60,
            ),
            eta_seconds=180,
        )

    return session_factory, settings


def test_run_generation_job_completes_and_writes_demo_artifacts(tmp_path: Path) -> None:
    session_factory, settings = _create_job_record(tmp_path)

    run_generation_job("job-1", {"ignored": True}, settings)

    with session_factory() as session:
        job = get_job(session, "job-1")
        artifacts = get_job_artifacts(session, "job-1")

    assert job is not None
    assert job.status == JobStatus.COMPLETED.value
    assert job.result_url == "/artifacts/job-1/mixed.wav"
    assert {artifact.kind for artifact in artifacts} == {
        "mixed",
        "vocals",
        "instrumental",
    }
    assert (tmp_path / "artifacts" / "job-1" / "mixed.wav").is_file()


def test_run_generation_job_marks_job_failed_when_artifact_generation_breaks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session_factory, settings = _create_job_record(tmp_path)

    from app import tasks

    def explode(*args, **kwargs):
        raise RuntimeError("artifact generation failed")

    monkeypatch.setattr(tasks, "_prepare_demo_artifacts", explode)

    with pytest.raises(RuntimeError, match="artifact generation failed"):
        run_generation_job("job-1", {"ignored": True}, settings)

    with session_factory() as session:
        job = get_job(session, "job-1")

    assert job is not None
    assert job.status == JobStatus.FAILED.value
    assert job.error_message == "artifact generation failed"
