"""Application logging configuration."""

import logging
import os
from logging.config import dictConfig

DEFAULT_LOG_LEVEL = "INFO"
SUPPORTED_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


def configure_logging() -> None:
    """Configure the process-wide logger from environment variables."""
    log_level = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    if log_level not in SUPPORTED_LOG_LEVELS:
        supported_levels = ", ".join(sorted(SUPPORTED_LOG_LEVELS))
        raise ValueError(f"LOG_LEVEL must be one of: {supported_levels}")

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {
                "handlers": ["console"],
                "level": log_level,
            },
        }
    )

    logging.getLogger(__name__).info("Logging configured at %s level", log_level)
