import pytest
from pydantic import ValidationError

from app.schemas import DEFAULT_SEGMENT_DURATION_SECONDS, GenerateSongRequest, WebSocketJobEnvelope


def test_generate_song_request_normalizes_aliases_and_derives_duration() -> None:
    payload = GenerateSongRequest(
        lyrics="la la la",
        genre_tags="dream-pop, airy, female vocal",
        segments=2,
    )

    assert payload.tags == ["dream-pop", "airy", "female vocal"]
    assert payload.duration == 2 * DEFAULT_SEGMENT_DURATION_SECONDS


def test_generate_song_request_requires_duration_or_segments() -> None:
    with pytest.raises(ValidationError, match="Provide duration or segments"):
        GenerateSongRequest(
            lyrics="la la la",
            tags=["indie"],
        )


def test_generate_song_request_requires_tags_or_style_prompt() -> None:
    with pytest.raises(ValidationError, match="Provide at least one tag or style_prompt"):
        GenerateSongRequest(
            lyrics="la la la",
            duration=60,
            tags=[],
        )


def test_generate_song_request_validates_dual_track_inputs() -> None:
    with pytest.raises(
        ValidationError,
        match="dual_track requires reference_audio_url or both",
    ):
        GenerateSongRequest(
            lyrics="la la la",
            duration=60,
            tags=["indie"],
            dual_track=True,
        )

    payload = GenerateSongRequest(
        lyrics="la la la",
        duration=60,
        tags=["indie"],
        dual_track=True,
        reference_vocal_url="https://files.example.com/vocals.mp3",
        reference_instrumental_url="https://files.example.com/instrumental.mp3",
    )

    assert payload.dual_track is True


def test_generate_song_request_rejects_invalid_prompt_window() -> None:
    with pytest.raises(ValidationError, match="prompt_end_time must be greater"):
        GenerateSongRequest(
            lyrics="la la la",
            duration=60,
            tags=["indie"],
            prompt_start_time=10,
            prompt_end_time=10,
        )


def test_websocket_job_envelope_clamps_progress() -> None:
    payload = WebSocketJobEnvelope(
        job_id="job-1",
        status="running",
        progress=5.0,
        eta_seconds=30,
    )

    assert payload.progress == 1.0
