from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


DEFAULT_SEGMENT_DURATION_SECONDS = 30


class GenerateSongRequest(BaseModel):
    lyrics: str = Field(..., min_length=1)
    tags: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("tags", "genre_tags"),
    )
    duration: int | None = Field(
        default=None,
        ge=15,
        le=600,
        validation_alias=AliasChoices("duration", "duration_seconds"),
    )
    segments: int | None = Field(
        default=None,
        ge=1,
        le=12,
        validation_alias=AliasChoices("segments", "run_n_segments"),
    )
    seed: int | None = Field(default=None, ge=0)
    reference_audio_url: str | None = None
    reference_vocal_url: str | None = None
    reference_instrumental_url: str | None = None
    dual_track: bool = False
    prompt_start_time: int = Field(default=0, ge=0)
    prompt_end_time: int | None = Field(default=None, ge=1)
    model_name: str = Field(default="lyrics2song-demo-v1", min_length=1, max_length=100)
    artist: str | None = Field(default=None, min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    style_prompt: str | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: str | list[str] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def validate_prompt_inputs(self) -> "GenerateSongRequest":
        if self.duration is None:
            if self.segments is None:
                raise ValueError("Provide duration or segments")
            self.duration = self.segments * DEFAULT_SEGMENT_DURATION_SECONDS

        if not self.tags and not self.style_prompt:
            raise ValueError("Provide at least one tag or style_prompt")

        if self.prompt_end_time is not None and self.prompt_end_time <= self.prompt_start_time:
            raise ValueError("prompt_end_time must be greater than prompt_start_time")

        if self.dual_track:
            if not (
                self.reference_audio_url
                or (
                    self.reference_vocal_url and self.reference_instrumental_url
                )
            ):
                raise ValueError(
                    "dual_track requires reference_audio_url or both reference_vocal_url and reference_instrumental_url"
                )
        elif self.reference_vocal_url or self.reference_instrumental_url:
            raise ValueError(
                "reference_vocal_url/reference_instrumental_url require dual_track=true"
            )

        return self


class JobError(BaseModel):
    message: str


class JobResultLinks(BaseModel):
    mixed_url: str
    vocals_url: str | None = None
    instrumental_url: str | None = None
    expires_at: datetime | None = None


class JobEnvelope(BaseModel):
    job_id: str
    status: str
    progress: float = Field(..., ge=0.0, le=1.0)
    eta_seconds: int | None = None
    model_name: str
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None
    result: JobResultLinks | None = None
    error: JobError | None = None


class ModelInfo(BaseModel):
    id: str
    provider: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelsResponse(BaseModel):
    models: list[ModelInfo]


class RateLimitResponse(BaseModel):
    key_fingerprint: str
    capacity: int
    remaining_tokens: float
    refill_per_second: float
    retry_after_seconds: float


class JobResultResponse(BaseModel):
    job_id: str
    signed_url: str
    expires_at: datetime
    files: dict[str, str] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    database: str
    redis: str


class WebSocketJobEnvelope(BaseModel):
    job_id: str
    status: str
    progress: float
    eta_seconds: int | None = None

    @field_validator("progress")
    @classmethod
    def clamp_progress(cls, value: float) -> float:
        return max(0.0, min(1.0, value))
