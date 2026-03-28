from collections.abc import Iterable
import logging
import math
from pathlib import Path
import struct
import time
import wave

from .config import Settings
from .db import create_session_factory
from .models import JobStatus
from .repository import replace_job_artifacts, update_job_state


LOGGER = logging.getLogger(__name__)


def _step_plan() -> Iterable[tuple[float, int]]:
    return (
        (0.15, 90),
        (0.45, 45),
        (0.75, 15),
        (1.0, 0),
    )


def _artifact_root(settings: Settings) -> Path:
    root = Path(settings.demo_artifacts_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifact_urls(job_id: str) -> dict[str, str]:
    return {
        "mixed": f"/artifacts/{job_id}/mixed.wav",
        "vocals": f"/artifacts/{job_id}/vocals.wav",
        "instrumental": f"/artifacts/{job_id}/instrumental.wav",
    }


def _write_demo_tone(path: Path, *, frequency_hz: float, duration_seconds: float = 1.8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 22_050
    amplitude = 12_000
    frame_count = int(sample_rate * duration_seconds)
    fade_frames = max(1, sample_rate // 20)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        for frame_index in range(frame_count):
            t = frame_index / sample_rate
            envelope = 1.0
            if frame_index < fade_frames:
                envelope = frame_index / fade_frames
            elif frame_index > frame_count - fade_frames:
                envelope = max(0.0, (frame_count - frame_index) / fade_frames)

            sample = int(
                amplitude
                * envelope
                * math.sin(2.0 * math.pi * frequency_hz * t)
            )
            wav_file.writeframes(struct.pack("<h", sample))


def _prepare_demo_artifacts(settings: Settings, job_id: str) -> dict[str, str]:
    job_dir = _artifact_root(settings) / job_id
    _write_demo_tone(job_dir / "mixed.wav", frequency_hz=440.0)
    _write_demo_tone(job_dir / "vocals.wav", frequency_hz=554.37)
    _write_demo_tone(job_dir / "instrumental.wav", frequency_hz=329.63)
    return _artifact_urls(job_id)


def run_generation_job(
    job_id: str,
    payload: dict[str, object],
    settings: Settings | None = None,
) -> None:
    _ = payload
    settings = settings or Settings()
    settings.ensure_sqlite_directory()
    session_factory = create_session_factory(settings.database_url)

    try:
        with session_factory() as session:
            update_job_state(
                session,
                job_id=job_id,
                status=JobStatus.RUNNING.value,
                progress=0.05,
                eta_seconds=120,
            )

        for progress, eta_seconds in _step_plan():
            time.sleep(settings.simulated_job_step_seconds)
            with session_factory() as session:
                if progress < 1.0:
                    update_job_state(
                        session,
                        job_id=job_id,
                        status=JobStatus.RUNNING.value,
                        progress=progress,
                        eta_seconds=eta_seconds,
                    )
                else:
                    result_files = _prepare_demo_artifacts(settings, job_id)
                    replace_job_artifacts(
                        session,
                        job_id=job_id,
                        files=result_files,
                    )
                    update_job_state(
                        session,
                        job_id=job_id,
                        status=JobStatus.COMPLETED.value,
                        progress=1.0,
                        eta_seconds=0,
                        result_url=result_files["mixed"],
                    )
    except Exception as exc:
        LOGGER.exception("Generation job failed", extra={"job_id": job_id})
        with session_factory() as session:
            update_job_state(
                session,
                job_id=job_id,
                status=JobStatus.FAILED.value,
                progress=1.0,
                eta_seconds=0,
                error_message=str(exc),
            )
        raise
