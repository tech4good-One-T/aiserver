"""Environment-backed application settings."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _csv(name: str) -> frozenset[str]:
    return frozenset(
        value.strip().lower() for value in os.getenv(name, "").split(",") if value.strip()
    )


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration loaded once at application startup."""

    app_env: str
    allowed_storage_hosts: frozenset[str]
    storage_bucket: str | None
    max_image_bytes: int
    max_image_dimension: int
    storage_timeout_seconds: int
    analysis_timeout_seconds: int
    processing_timeout_seconds: int
    prompt_edit_timeout_seconds: int
    gemini_api_key: str | None
    gemini_model: str
    gemini_image_model: str
    faceshield_repo_path: Path | None
    faceshield_command: str

    @classmethod
    def from_env(cls) -> "Settings":
        """Build validated settings from process environment variables."""
        repo_path = os.getenv("FACESHIELD_REPO_PATH", "").strip()
        return cls(
            app_env=os.getenv("APP_ENV", "local").strip().lower(),
            allowed_storage_hosts=_csv("ALLOWED_STORAGE_HOSTS"),
            storage_bucket=os.getenv("STORAGE_BUCKET", "").strip() or None,
            max_image_bytes=_positive_int("MAX_IMAGE_BYTES", 10 * 1024 * 1024),
            max_image_dimension=_positive_int("MAX_IMAGE_DIMENSION", 4096),
            storage_timeout_seconds=_positive_int("STORAGE_TIMEOUT_SECONDS", 30),
            analysis_timeout_seconds=_positive_int("ANALYSIS_TIMEOUT_SECONDS", 60),
            processing_timeout_seconds=_positive_int("PROCESSING_TIMEOUT_SECONDS", 600),
            prompt_edit_timeout_seconds=_positive_int("PROMPT_EDIT_TIMEOUT_SECONDS", 600),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip() or None,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip(),
            gemini_image_model=os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image").strip(),
            faceshield_repo_path=Path(repo_path).expanduser().resolve() if repo_path else None,
            faceshield_command=os.getenv(
                "FACESHIELD_COMMAND",
                "conda run --no-capture-output -n faceshield bash execute.sh",
            ).strip(),
        )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable settings object."""
    return Settings.from_env()
