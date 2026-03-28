from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
import logging
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import boto3
from botocore.config import Config as BotoConfig
import httpx
from redis import Redis
from rq import Worker
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    insert,
    update,
)


LOGGER = logging.getLogger("lyrics2song.gpu-worker")
_FLAG_SUPPORT_CACHE: dict[tuple[str, str, str], bool] = {}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _replace_url_path(url: str, new_path: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


@dataclass(slots=True)
class Settings:
    redis_url: str
    queue_name: str
    database_url: str
    progress_channel: str
    s3_bucket: str
    s3_endpoint_url: str | None
    s3_access_key_id: str | None
    s3_secret_access_key: str | None
    s3_session_token: str | None
    s3_region: str | None
    s3_public_base_url: str | None
    s3_prefix: str
    s3_force_path_style: bool
    yue_runner: str
    yue_python_runner: str | None
    yue_python_executable: str
    yue_workdir: str | None
    yue_infer_script: str
    yue_stage1_model: str
    yue_stage2_model: str
    yue_cuda_idx: int
    yue_stage2_batch_size: int
    yue_max_new_tokens: int
    yue_repetition_penalty: float
    yue_segment_seconds: int
    yue_default_prompt_end_time: int
    yue_seed_flag: str
    yue_extra_args: list[str]
    http_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            queue_name=os.getenv("QUEUE_NAME", "lyrics2song-jobs"),
            database_url=os.getenv(
                "DATABASE_URL",
                "sqlite:///./data/lyrics2song.db",
            ),
            progress_channel=os.getenv(
                "PROGRESS_CHANNEL",
                "lyrics2song-job-progress",
            ),
            s3_bucket=os.getenv("S3_BUCKET", ""),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
            s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID") or None,
            s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY") or None,
            s3_session_token=os.getenv("S3_SESSION_TOKEN") or None,
            s3_region=os.getenv("S3_REGION") or None,
            s3_public_base_url=os.getenv("S3_PUBLIC_BASE_URL") or None,
            s3_prefix=os.getenv("S3_PREFIX", "results"),
            s3_force_path_style=_env_bool("S3_FORCE_PATH_STYLE", True),
            yue_runner=os.getenv("YUE_RUNNER", "cli").strip().lower(),
            yue_python_runner=os.getenv("YUE_PYTHON_RUNNER") or None,
            yue_python_executable=os.getenv("YUE_PYTHON_EXECUTABLE", sys.executable),
            yue_workdir=os.getenv("YUE_WORKDIR") or None,
            yue_infer_script=os.getenv("YUE_INFER_SCRIPT", "infer.py"),
            yue_stage1_model=os.getenv(
                "YUE_STAGE1_MODEL",
                "m-a-p/YuE-s1-7B-anneal-en-icl",
            ),
            yue_stage2_model=os.getenv(
                "YUE_STAGE2_MODEL",
                "m-a-p/YuE-s2-1B-general",
            ),
            yue_cuda_idx=int(os.getenv("YUE_CUDA_IDX", "0")),
            yue_stage2_batch_size=int(os.getenv("YUE_STAGE2_BATCH_SIZE", "4")),
            yue_max_new_tokens=int(os.getenv("YUE_MAX_NEW_TOKENS", "3000")),
            yue_repetition_penalty=float(
                os.getenv("YUE_REPETITION_PENALTY", "1.1")
            ),
            yue_segment_seconds=int(os.getenv("YUE_SEGMENT_SECONDS", "30")),
            yue_default_prompt_end_time=int(
                os.getenv("YUE_DEFAULT_PROMPT_END_TIME", "30")
            ),
            yue_seed_flag=os.getenv("YUE_SEED_FLAG", "--seed"),
            yue_extra_args=shlex.split(os.getenv("YUE_EXTRA_ARGS", "")),
            http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "60")),
        )

    def ensure_sqlite_directory(self) -> None:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return
        raw_path = self.database_url[len(prefix) :]
        if raw_path == ":memory:":
            return
        db_path = Path(raw_path)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)


def _build_engine(database_url: str):
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=True,
    )


class JobStateStore:
    def __init__(self, settings: Settings) -> None:
        settings.ensure_sqlite_directory()
        self.settings = settings
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.engine = _build_engine(settings.database_url)
        self.metadata = MetaData()
        self.jobs = Table("jobs", self.metadata, autoload_with=self.engine)
        self.job_artifacts = Table(
            "job_artifacts",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("job_id", String(36), nullable=False, index=True),
            Column("kind", String(32), nullable=False),
            Column("url", Text, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            UniqueConstraint("job_id", "kind", name="uq_job_artifacts_job_kind"),
            extend_existing=True,
        )
        self.metadata.create_all(self.engine, tables=[self.job_artifacts])

    def update_state(
        self,
        *,
        job_id: str,
        status: str | None = None,
        progress: float | None = None,
        eta_seconds: int | None = None,
        result_url: str | None = None,
        error_message: str | None = None,
        result_files: dict[str, str] | None = None,
        message: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        values: dict[str, Any] = {"updated_at": now}

        if status is not None:
            values["status"] = status
        if progress is not None:
            values["progress"] = max(0.0, min(1.0, progress))
        if eta_seconds is not None:
            values["eta_seconds"] = eta_seconds
        if result_url is not None:
            values["result_url"] = result_url
        elif result_files and "mixed" in result_files:
            values["result_url"] = result_files["mixed"]
        if error_message is not None:
            values["error_message"] = error_message

        with self.engine.begin() as connection:
            result = connection.execute(
                update(self.jobs)
                .where(self.jobs.c.id == job_id)
                .values(**values)
            )
            if result.rowcount == 0:
                raise RuntimeError(f"Job '{job_id}' was not found in the database")

            if result_files is not None:
                connection.execute(
                    delete(self.job_artifacts).where(
                        self.job_artifacts.c.job_id == job_id
                    )
                )
                if result_files:
                    rows = [
                        {
                            "job_id": job_id,
                            "kind": kind,
                            "url": url,
                            "created_at": now,
                            "updated_at": now,
                        }
                        for kind, url in result_files.items()
                    ]
                    connection.execute(insert(self.job_artifacts), rows)

        payload = {
            "job_id": job_id,
            "status": status,
            "progress": values.get("progress"),
            "eta_seconds": eta_seconds,
            "result_url": values.get("result_url"),
            "result_files": result_files or {},
            "error_message": error_message,
            "message": message,
            "updated_at": now.isoformat(),
        }
        message_body = json.dumps(payload, ensure_ascii=True)
        self.redis.publish(self.settings.progress_channel, message_body)
        self.redis.publish(f"{self.settings.progress_channel}:{job_id}", message_body)


class S3Uploader:
    def __init__(self, settings: Settings) -> None:
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required for artifact uploads")

        addressing_style = "path" if settings.s3_force_path_style else "auto"
        self.settings = settings
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            aws_session_token=settings.s3_session_token,
            config=BotoConfig(s3={"addressing_style": addressing_style}),
        )

    def upload_files(
        self,
        *,
        job_id: str,
        files: dict[str, Path],
    ) -> dict[str, str]:
        uploaded: dict[str, str] = {}
        for kind, local_path in files.items():
            key = self._object_key(job_id, f"{kind}.mp3")
            self.client.upload_file(
                str(local_path),
                self.settings.s3_bucket,
                key,
                ExtraArgs={"ContentType": "audio/mpeg"},
            )
            uploaded[kind] = self._public_url(key)
        return uploaded

    def _object_key(self, job_id: str, filename: str) -> str:
        return "/".join(
            item.strip("/")
            for item in (self.settings.s3_prefix, job_id, filename)
            if item
        )

    def _public_url(self, key: str) -> str:
        encoded_key = quote(key, safe="/")
        if self.settings.s3_public_base_url:
            return f"{self.settings.s3_public_base_url.rstrip('/')}/{encoded_key}"
        if self.settings.s3_endpoint_url:
            return (
                f"{self.settings.s3_endpoint_url.rstrip('/')}/"
                f"{self.settings.s3_bucket}/{encoded_key}"
            )
        return f"https://{self.settings.s3_bucket}.s3.amazonaws.com/{encoded_key}"


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    lyrics = str(payload.get("lyrics", "")).strip()
    if not lyrics:
        raise ValueError("lyrics is required")

    tags = payload.get("tags", [])
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.split(",") if item.strip()]
    elif isinstance(tags, list):
        tags = [str(item).strip() for item in tags if str(item).strip()]
    else:
        raise ValueError("tags must be a list or comma-separated string")

    style_prompt = payload.get("style_prompt")
    if style_prompt is not None:
        style_prompt = str(style_prompt).strip() or None

    if not tags and not style_prompt:
        raise ValueError("Provide at least one tag or style_prompt")

    duration = payload.get("duration", payload.get("duration_seconds"))
    if duration is None:
        raise ValueError("duration is required")

    duration_value = int(duration)
    segments = payload.get("segments", payload.get("run_n_segments"))
    segments_value = int(segments) if segments is not None else None
    if segments_value is not None and segments_value < 1:
        raise ValueError("segments must be greater than or equal to 1")

    prompt_start_time = int(payload.get("prompt_start_time", 0))
    prompt_end_time = payload.get("prompt_end_time")
    prompt_end_value = int(prompt_end_time) if prompt_end_time is not None else None
    if prompt_end_value is not None and prompt_end_value <= prompt_start_time:
        raise ValueError("prompt_end_time must be greater than prompt_start_time")

    seed = payload.get("seed")
    dual_track = bool(payload.get("dual_track", False))
    reference_audio_url = payload.get("reference_audio_url")
    reference_vocal_url = payload.get("reference_vocal_url")
    reference_instrumental_url = payload.get("reference_instrumental_url")

    if dual_track and not (
        reference_audio_url
        or (reference_vocal_url and reference_instrumental_url)
    ):
        raise ValueError(
            "dual_track requires reference_audio_url or both reference_vocal_url and reference_instrumental_url"
        )
    if not dual_track and (reference_vocal_url or reference_instrumental_url):
        raise ValueError(
            "reference_vocal_url/reference_instrumental_url require dual_track=true"
        )

    return {
        "lyrics": lyrics,
        "tags": tags,
        "duration": duration_value,
        "segments": segments_value,
        "seed": int(seed) if seed is not None else None,
        "reference_audio_url": reference_audio_url,
        "reference_vocal_url": reference_vocal_url,
        "reference_instrumental_url": reference_instrumental_url,
        "dual_track": dual_track,
        "prompt_start_time": prompt_start_time,
        "prompt_end_time": prompt_end_value,
        "model_name": payload.get("model_name"),
        "artist": payload.get("artist"),
        "title": payload.get("title"),
        "style_prompt": style_prompt,
    }


def _build_genre_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    if payload["tags"]:
        lines.append(", ".join(payload["tags"]))
    if payload["style_prompt"]:
        lines.append(payload["style_prompt"])
    return "\n".join(lines)


def _write_prompt_files(job_dir: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    genre_path = job_dir / "genre.txt"
    lyrics_path = job_dir / "lyrics.txt"
    genre_path.write_text(_build_genre_text(payload), encoding="utf-8")
    lyrics_path.write_text(payload["lyrics"], encoding="utf-8")
    return genre_path, lyrics_path


def _estimate_eta_seconds(payload: dict[str, Any]) -> int:
    return max(60, math.ceil(payload["duration"] * 2.5))


def _cli_supports_flag(settings: Settings, flag: str) -> bool:
    if not flag:
        return False

    cache_key = (
        settings.yue_python_executable,
        settings.yue_infer_script,
        flag,
    )
    cached = _FLAG_SUPPORT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        result = subprocess.run(
            [
                settings.yue_python_executable,
                settings.yue_infer_script,
                "--help",
            ],
            cwd=settings.yue_workdir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        _FLAG_SUPPORT_CACHE[cache_key] = False
        return False

    combined_output = f"{result.stdout}\n{result.stderr}"
    supported = flag in combined_output
    _FLAG_SUPPORT_CACHE[cache_key] = supported
    return supported


def _download_file(client: httpx.Client, url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    return destination


def _load_dual_track_manifest(
    client: httpx.Client,
    manifest_url: str,
) -> tuple[str, str]:
    response = client.get(manifest_url)
    response.raise_for_status()
    payload = response.json()
    vocal_url = (
        payload.get("reference_vocal_url")
        or payload.get("vocal_url")
        or payload.get("vocals")
        or payload.get("vocal")
    )
    instrumental_url = (
        payload.get("reference_instrumental_url")
        or payload.get("instrumental_url")
        or payload.get("instrumental")
    )
    if not vocal_url or not instrumental_url:
        raise ValueError(
            "Dual-track manifest must contain vocal/instrumental URLs"
        )
    return str(vocal_url), str(instrumental_url)


def _dual_track_candidates(base_url: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    if base_url.endswith("/"):
        candidates.extend(
            [
                (f"{base_url}vocals.mp3", f"{base_url}instrumental.mp3"),
                (f"{base_url}Vocals.mp3", f"{base_url}Instrumental.mp3"),
            ]
        )
        return candidates

    parts = urlsplit(base_url)
    path = parts.path
    path_parent = path.rsplit("/", 1)[0] if "/" in path else ""
    suffix = Path(path).suffix or ".mp3"

    if path.lower().endswith(".mp3"):
        stem_path = path[: -len(suffix)]
        candidates.extend(
            [
                (
                    _replace_url_path(base_url, f"{stem_path}.Vocals{suffix}"),
                    _replace_url_path(base_url, f"{stem_path}.Instrumental{suffix}"),
                ),
                (
                    _replace_url_path(base_url, f"{stem_path}.vocals{suffix}"),
                    _replace_url_path(base_url, f"{stem_path}.instrumental{suffix}"),
                ),
            ]
        )

    shared_prefix = f"{path_parent}/" if path_parent else "/"
    candidates.extend(
        [
            (
                _replace_url_path(base_url, f"{shared_prefix}vocals{suffix}"),
                _replace_url_path(base_url, f"{shared_prefix}instrumental{suffix}"),
            ),
            (
                _replace_url_path(base_url, f"{shared_prefix}Vocals{suffix}"),
                _replace_url_path(base_url, f"{shared_prefix}Instrumental{suffix}"),
            ),
        ]
    )

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def _prepare_reference_args(
    *,
    settings: Settings,
    payload: dict[str, Any],
    job_dir: Path,
) -> list[str]:
    reference_audio_url = payload["reference_audio_url"]
    if not reference_audio_url and not payload["dual_track"]:
        return []

    prompt_end_time = payload["prompt_end_time"]
    if prompt_end_time is None:
        prompt_end_time = min(
            payload["duration"],
            settings.yue_default_prompt_end_time,
        )

    common_args = [
        "--prompt_start_time",
        str(payload["prompt_start_time"]),
        "--prompt_end_time",
        str(prompt_end_time),
    ]

    with httpx.Client(
        follow_redirects=True,
        timeout=settings.http_timeout_seconds,
    ) as client:
        if payload["dual_track"]:
            vocal_url = payload["reference_vocal_url"]
            instrumental_url = payload["reference_instrumental_url"]

            if reference_audio_url and str(reference_audio_url).lower().endswith(".json"):
                vocal_url, instrumental_url = _load_dual_track_manifest(
                    client,
                    str(reference_audio_url),
                )

            if vocal_url and instrumental_url:
                vocal_path = _download_file(
                    client,
                    str(vocal_url),
                    job_dir / "reference" / "vocals.mp3",
                )
                instrumental_path = _download_file(
                    client,
                    str(instrumental_url),
                    job_dir / "reference" / "instrumental.mp3",
                )
            else:
                if not reference_audio_url:
                    raise ValueError(
                        "dual_track resolution requires reference_audio_url"
                    )
                last_error: Exception | None = None
                for candidate_vocal, candidate_instrumental in _dual_track_candidates(
                    str(reference_audio_url)
                ):
                    try:
                        vocal_path = _download_file(
                            client,
                            candidate_vocal,
                            job_dir / "reference" / "vocals.mp3",
                        )
                        instrumental_path = _download_file(
                            client,
                            candidate_instrumental,
                            job_dir / "reference" / "instrumental.mp3",
                        )
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        (job_dir / "reference" / "vocals.mp3").unlink(missing_ok=True)
                        (job_dir / "reference" / "instrumental.mp3").unlink(
                            missing_ok=True
                        )
                else:
                    raise ValueError(
                        "Unable to resolve dual-track reference audio. "
                        "Provide reference_vocal_url/reference_instrumental_url, "
                        "or make reference_audio_url point to a manifest or prefix."
                    ) from last_error

            return [
                "--use_dual_tracks_prompt",
                "--vocal_track_prompt_path",
                str(vocal_path),
                "--instrumental_track_prompt_path",
                str(instrumental_path),
                *common_args,
            ]

        reference_path = _download_file(
            client,
            str(reference_audio_url),
            job_dir / "reference" / "reference_audio.mp3",
        )
        return [
            "--use_audio_prompt",
            "--audio_prompt_path",
            str(reference_path),
            *common_args,
        ]


def _build_cli_command(
    *,
    settings: Settings,
    payload: dict[str, Any],
    genre_path: Path,
    lyrics_path: Path,
    output_dir: Path,
    reference_args: list[str],
) -> list[str]:
    run_n_segments = payload["segments"] or max(
        1,
        math.ceil(payload["duration"] / settings.yue_segment_seconds),
    )
    command = [
        settings.yue_python_executable,
        settings.yue_infer_script,
        "--cuda_idx",
        str(settings.yue_cuda_idx),
        "--stage1_model",
        settings.yue_stage1_model,
        "--stage2_model",
        settings.yue_stage2_model,
        "--genre_txt",
        str(genre_path),
        "--lyrics_txt",
        str(lyrics_path),
        "--run_n_segments",
        str(run_n_segments),
        "--stage2_batch_size",
        str(settings.yue_stage2_batch_size),
        "--output_dir",
        str(output_dir),
        "--max_new_tokens",
        str(settings.yue_max_new_tokens),
        "--repetition_penalty",
        str(settings.yue_repetition_penalty),
        *settings.yue_extra_args,
        *reference_args,
    ]

    if payload["seed"] is not None:
        if settings.yue_seed_flag and _cli_supports_flag(settings, settings.yue_seed_flag):
            command.extend([settings.yue_seed_flag, str(payload["seed"])])
        else:
            LOGGER.warning(
                "Seed was provided for job but YuE CLI does not advertise %s; skipping seed",
                settings.yue_seed_flag,
            )

    return command


def _run_cli_inference(
    *,
    settings: Settings,
    payload: dict[str, Any],
    genre_path: Path,
    lyrics_path: Path,
    output_dir: Path,
    reference_args: list[str],
) -> None:
    command = _build_cli_command(
        settings=settings,
        payload=payload,
        genre_path=genre_path,
        lyrics_path=lyrics_path,
        output_dir=output_dir,
        reference_args=reference_args,
    )
    LOGGER.info("Running YuE inference command: %s", shlex.join(command))

    process = subprocess.Popen(
        command,
        cwd=settings.yue_workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        LOGGER.info("YuE | %s", line.rstrip())

    exit_code = process.wait()
    if exit_code != 0:
        raise RuntimeError(f"YuE inference failed with exit code {exit_code}")


def _run_python_inference(
    *,
    settings: Settings,
    payload: dict[str, Any],
    genre_path: Path,
    lyrics_path: Path,
    output_dir: Path,
    reference_args: list[str],
) -> None:
    if not settings.yue_python_runner or ":" not in settings.yue_python_runner:
        raise RuntimeError(
            "YUE_PYTHON_RUNNER must be set to module:function when YUE_RUNNER=python"
        )

    module_name, function_name = settings.yue_python_runner.split(":", 1)
    module = importlib.import_module(module_name)
    runner = getattr(module, function_name)
    runner(
        payload=payload,
        genre_path=str(genre_path),
        lyrics_path=str(lyrics_path),
        output_dir=str(output_dir),
        reference_args=reference_args,
        settings=settings,
    )


def _collect_output_files(output_dir: Path) -> dict[str, Path]:
    audio_files = sorted(
        [path for path in output_dir.rglob("*.mp3") if path.is_file()],
        key=lambda path: (path.stat().st_mtime, len(path.as_posix())),
        reverse=True,
    )
    if not audio_files:
        raise FileNotFoundError(f"No MP3 outputs were found in {output_dir}")

    lower_name_map = {path.name.lower(): path for path in audio_files}
    selected: dict[str, Path] = {}

    exact_names = {
        "mixed": "mixed.mp3",
        "vocals": "vocals.mp3",
        "instrumental": "instrumental.mp3",
    }
    for kind, filename in exact_names.items():
        if filename in lower_name_map:
            selected[kind] = lower_name_map[filename]

    keyword_map = {
        "mixed": ("mixed", "mix", "full"),
        "vocals": ("vocals", "vocal"),
        "instrumental": ("instrumental", "inst", "no_vocals"),
    }
    excluded_keywords = {
        "vocals": ("no_vocals", "instrumental"),
    }

    for kind, keywords in keyword_map.items():
        if kind in selected:
            continue
        for candidate in audio_files:
            name = candidate.name.lower()
            if any(keyword in name for keyword in keywords):
                blocked = excluded_keywords.get(kind, ())
                if any(keyword in name for keyword in blocked):
                    continue
                selected[kind] = candidate
                break

    missing = {"mixed", "vocals", "instrumental"} - set(selected)
    if missing:
        raise FileNotFoundError(
            f"Expected mixed/vocals/instrumental outputs, missing: {sorted(missing)}"
        )
    return selected


def run_generation_job(job_id: str, payload: dict[str, Any]) -> None:
    settings = Settings.from_env()
    state_store: JobStateStore | None = None

    try:
        state_store = JobStateStore(settings)
        normalized_payload = _normalize_payload(payload)
        uploader = S3Uploader(settings)
        eta_seconds = _estimate_eta_seconds(normalized_payload)

        state_store.update_state(
            job_id=job_id,
            status="running",
            progress=0.05,
            eta_seconds=eta_seconds,
            message="Preparing YuE inputs",
        )

        with tempfile.TemporaryDirectory(prefix=f"lyrics2song-{job_id}-") as temp_dir:
            job_dir = Path(temp_dir)
            output_dir = job_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            genre_path, lyrics_path = _write_prompt_files(job_dir, normalized_payload)
            reference_args: list[str] = []

            if normalized_payload["reference_audio_url"] or normalized_payload["dual_track"]:
                state_store.update_state(
                    job_id=job_id,
                    status="running",
                    progress=0.15,
                    eta_seconds=max(10, eta_seconds - 15),
                    message="Downloading reference audio",
                )
                reference_args = _prepare_reference_args(
                    settings=settings,
                    payload=normalized_payload,
                    job_dir=job_dir,
                )

            state_store.update_state(
                job_id=job_id,
                status="running",
                progress=0.3,
                eta_seconds=max(15, eta_seconds - 30),
                message="Running YuE inference",
            )

            if settings.yue_runner == "python":
                _run_python_inference(
                    settings=settings,
                    payload=normalized_payload,
                    genre_path=genre_path,
                    lyrics_path=lyrics_path,
                    output_dir=output_dir,
                    reference_args=reference_args,
                )
            elif settings.yue_runner == "cli":
                _run_cli_inference(
                    settings=settings,
                    payload=normalized_payload,
                    genre_path=genre_path,
                    lyrics_path=lyrics_path,
                    output_dir=output_dir,
                    reference_args=reference_args,
                )
            else:
                raise RuntimeError(f"Unsupported YUE_RUNNER '{settings.yue_runner}'")

            local_outputs = _collect_output_files(output_dir)
            state_store.update_state(
                job_id=job_id,
                status="running",
                progress=0.9,
                eta_seconds=10,
                message="Uploading outputs to storage",
            )

            uploaded_files = uploader.upload_files(job_id=job_id, files=local_outputs)
            state_store.update_state(
                job_id=job_id,
                status="completed",
                progress=1.0,
                eta_seconds=0,
                result_files=uploaded_files,
                result_url=uploaded_files["mixed"],
                message="Generation completed",
            )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Generation job failed", extra={"job_id": job_id})
        if state_store is not None:
            try:
                state_store.update_state(
                    job_id=job_id,
                    status="failed",
                    progress=1.0,
                    eta_seconds=0,
                    error_message=str(exc),
                    message="Generation failed",
                )
            except Exception:  # noqa: BLE001
                LOGGER.exception("Failed to persist terminal job failure state")
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="lyrics2song GPU worker")
    parser.add_argument(
        "--burst",
        action="store_true",
        help="Process all currently queued jobs and exit.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Python logging level.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings = Settings.from_env()
    redis = Redis.from_url(settings.redis_url)
    worker = Worker([settings.queue_name], connection=redis)
    worker.work(with_scheduler=False, burst=bool(args.burst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
