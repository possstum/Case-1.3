from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "lyrics2song control-plane"
    environment: str = "dev"
    database_url: str = "sqlite:///./data/lyrics2song.db"
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "lyrics2song-jobs"
    queue_mode: str = "rq"
    queue_job_timeout_seconds: int = 7200
    queue_result_ttl_seconds: int = 86400
    api_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["dev-api-key"]
    )
    jwt_secret: str | None = None
    jwt_algorithm: str = "HS256"
    rate_limit_capacity: int = 20
    rate_limit_refill_per_second: float = 1.0
    default_eta_seconds: int = 180
    available_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["lyrics2song-demo-v1"]
    )
    result_base_url: str = "https://storage.example.com/results"
    signed_url_secret: str = "dev-signed-url-secret"
    signed_url_ttl_seconds: int = 900
    websocket_poll_interval_seconds: float = 1.0
    simulated_job_step_seconds: float = 0.35
    demo_artifacts_dir: str = "./data/demo-artifacts"

    @field_validator("api_keys", mode="before")
    @classmethod
    def split_api_keys(cls, value: str | list[str] | None) -> list[str]:
        if value is None:
            return ["dev-api-key"]
        if isinstance(value, str):
            keys = [item.strip() for item in value.split(",") if item.strip()]
            return keys or ["dev-api-key"]
        keys = [item.strip() for item in value if item.strip()]
        return keys or ["dev-api-key"]

    @field_validator("available_models", mode="before")
    @classmethod
    def split_models(cls, value: str | list[str] | None) -> list[str]:
        if value is None:
            return ["lyrics2song-demo-v1"]
        if isinstance(value, str):
            models = [item.strip() for item in value.split(",") if item.strip()]
            return models or ["lyrics2song-demo-v1"]
        models = [item.strip() for item in value if item.strip()]
        return models or ["lyrics2song-demo-v1"]

    def ensure_sqlite_directory(self) -> None:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            Path(self.demo_artifacts_dir).mkdir(parents=True, exist_ok=True)
            return
        raw_path = self.database_url[len(prefix) :]
        if raw_path != ":memory:":
            db_path = Path(raw_path)
            if not db_path.is_absolute():
                db_path = Path.cwd() / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
        Path(self.demo_artifacts_dir).mkdir(parents=True, exist_ok=True)
