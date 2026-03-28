from pathlib import Path
import json
import sqlite3

import pytest


def test_normalize_payload_accepts_style_prompt_without_tags(worker_module) -> None:
    payload = worker_module._normalize_payload(
        {
            "lyrics": "la la la",
            "duration": 75,
            "style_prompt": "warm analog synths",
            "seed": 7,
        }
    )

    assert payload["lyrics"] == "la la la"
    assert payload["tags"] == []
    assert payload["style_prompt"] == "warm analog synths"
    assert payload["seed"] == 7


def test_normalize_payload_rejects_reference_tracks_without_dual_track(worker_module) -> None:
    with pytest.raises(ValueError, match="require dual_track=true"):
        worker_module._normalize_payload(
            {
                "lyrics": "la la la",
                "duration": 60,
                "tags": ["indie"],
                "reference_vocal_url": "https://files.example.com/vocals.mp3",
            }
        )


def test_load_dual_track_manifest_accepts_aliases(worker_module) -> None:
    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {
                "vocals": "https://files.example.com/vocals.mp3",
                "instrumental": "https://files.example.com/instrumental.mp3",
            }

    class DummyClient:
        def get(self, url: str) -> DummyResponse:
            assert url == "https://files.example.com/manifest.json"
            return DummyResponse()

    vocal_url, instrumental_url = worker_module._load_dual_track_manifest(
        DummyClient(),
        "https://files.example.com/manifest.json",
    )

    assert vocal_url.endswith("/vocals.mp3")
    assert instrumental_url.endswith("/instrumental.mp3")


def test_dual_track_candidates_include_prefix_variants(worker_module) -> None:
    candidates = worker_module._dual_track_candidates("https://files.example.com/ref/")

    assert (
        "https://files.example.com/ref/vocals.mp3",
        "https://files.example.com/ref/instrumental.mp3",
    ) in candidates
    assert len(candidates) == len(set(candidates))


def test_build_cli_command_adds_seed_when_supported(
    monkeypatch,
    worker_module,
    make_settings,
    tmp_path: Path,
) -> None:
    settings = make_settings(yue_extra_args=["--temperature", "0.8"])
    monkeypatch.setattr(worker_module, "_cli_supports_flag", lambda *args: True)
    payload = worker_module._normalize_payload(
        {
            "lyrics": "la la la",
            "duration": 61,
            "tags": ["indie"],
            "seed": 11,
        }
    )

    command = worker_module._build_cli_command(
        settings=settings,
        payload=payload,
        genre_path=tmp_path / "genre.txt",
        lyrics_path=tmp_path / "lyrics.txt",
        output_dir=tmp_path / "output",
        reference_args=[],
    )

    assert command[:2] == ["python", "infer.py"]
    assert "--run_n_segments" in command
    assert command[command.index("--run_n_segments") + 1] == "3"
    assert "--seed" in command
    assert command[command.index("--seed") + 1] == "11"
    assert "--temperature" in command


def test_build_cli_command_skips_seed_when_cli_does_not_support_it(
    monkeypatch,
    worker_module,
    make_settings,
    tmp_path: Path,
) -> None:
    settings = make_settings()
    monkeypatch.setattr(worker_module, "_cli_supports_flag", lambda *args: False)
    payload = worker_module._normalize_payload(
        {
            "lyrics": "la la la",
            "duration": 60,
            "tags": ["indie"],
            "seed": 11,
        }
    )

    command = worker_module._build_cli_command(
        settings=settings,
        payload=payload,
        genre_path=tmp_path / "genre.txt",
        lyrics_path=tmp_path / "lyrics.txt",
        output_dir=tmp_path / "output",
        reference_args=[],
    )

    assert "--seed" not in command


def test_collect_output_files_selects_mixed_vocals_and_instrumental(
    worker_module,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    for filename in ("mix_final.mp3", "lead_vocals.mp3", "no_vocals.mp3"):
        (output_dir / filename).write_bytes(b"mp3")

    selected = worker_module._collect_output_files(output_dir)

    assert selected["mixed"].name == "mix_final.mp3"
    assert selected["vocals"].name == "lead_vocals.mp3"
    assert selected["instrumental"].name == "no_vocals.mp3"


def test_validate_cli_runtime_rejects_missing_workdir(
    worker_module,
    make_settings,
    tmp_path: Path,
) -> None:
    settings = make_settings(yue_workdir=str(tmp_path / "opt" / "yue"))

    with pytest.raises(RuntimeError, match="YUE_WORKDIR does not exist"):
        worker_module._validate_cli_runtime(settings)


def test_validate_cli_runtime_rejects_missing_infer_script(
    worker_module,
    make_settings,
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "opt" / "yue"
    workdir.mkdir(parents=True)
    settings = make_settings(yue_workdir=str(workdir))

    with pytest.raises(RuntimeError, match="YuE infer script does not exist"):
        worker_module._validate_cli_runtime(settings)


def test_validate_cli_runtime_resolves_valid_infer_script(
    worker_module,
    make_settings,
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "opt" / "yue"
    workdir.mkdir(parents=True)
    infer_script = workdir / "infer.py"
    infer_script.write_text("print('ok')\n", encoding="utf-8")
    settings = make_settings(yue_workdir=str(workdir))

    resolved_workdir, resolved_infer = worker_module._validate_cli_runtime(settings)

    assert resolved_workdir == workdir.resolve()
    assert resolved_infer == infer_script.resolve()
    assert settings.yue_workdir == str(workdir.resolve())
    assert settings.yue_infer_script == str(infer_script.resolve())


def test_main_fails_fast_before_connecting_to_redis_when_runtime_is_missing(
    monkeypatch,
    worker_module,
) -> None:
    args = type("Args", (), {"log_level": "INFO", "burst": True})()
    redis_called = False

    monkeypatch.setattr(worker_module, "parse_args", lambda: args)

    def _unexpected_redis(*args, **kwargs):
        nonlocal redis_called
        redis_called = True
        raise AssertionError("Redis should not be initialized")

    monkeypatch.setattr(worker_module.Redis, "from_url", _unexpected_redis)
    monkeypatch.setenv("YUE_RUNNER", "cli")
    monkeypatch.setenv("YUE_WORKDIR", "/does/not/exist")

    with pytest.raises(RuntimeError, match="YUE_WORKDIR does not exist"):
        worker_module.main()

    assert redis_called is False


def test_job_state_store_updates_database_and_publishes_events(
    monkeypatch,
    worker_module,
    make_settings,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "worker-store.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            status TEXT,
            progress REAL,
            eta_seconds INTEGER,
            result_url TEXT,
            error_message TEXT,
            updated_at TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO jobs (id, status, progress, eta_seconds, result_url, error_message, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("job-1", "queued", 0.0, 180, None, None, "2026-03-27T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    published: list[tuple[str, str]] = []

    class DummyRedis:
        def publish(self, channel: str, payload: str) -> None:
            published.append((channel, payload))

    monkeypatch.setattr(
        worker_module.Redis,
        "from_url",
        lambda *args, **kwargs: DummyRedis(),
    )

    settings = make_settings(database_url=f"sqlite:///{database_path}")
    store = worker_module.JobStateStore(settings)
    store.update_state(
        job_id="job-1",
        status="completed",
        progress=1.2,
        eta_seconds=0,
        result_files={
            "mixed": "https://cdn.example.com/results/job-1/mixed.mp3",
            "vocals": "https://cdn.example.com/results/job-1/vocals.mp3",
        },
        message="done",
    )

    connection = sqlite3.connect(database_path)
    job_row = connection.execute(
        "SELECT status, progress, eta_seconds, result_url FROM jobs WHERE id = ?",
        ("job-1",),
    ).fetchone()
    artifact_rows = connection.execute(
        "SELECT kind, url FROM job_artifacts ORDER BY kind",
    ).fetchall()
    connection.close()

    assert job_row == (
        "completed",
        1.0,
        0,
        "https://cdn.example.com/results/job-1/mixed.mp3",
    )
    assert artifact_rows == [
        ("mixed", "https://cdn.example.com/results/job-1/mixed.mp3"),
        ("vocals", "https://cdn.example.com/results/job-1/vocals.mp3"),
    ]
    assert [channel for channel, _ in published] == [
        "lyrics2song-job-progress",
        "lyrics2song-job-progress:job-1",
    ]
    event = json.loads(published[0][1])
    assert event["status"] == "completed"
    assert event["result_files"]["mixed"].endswith("/mixed.mp3")
