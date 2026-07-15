import logging

import pytest

from app.core.logging import configure_logging


def test_configure_logging_uses_environment_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")

    configure_logging()

    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_rejects_unknown_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(ValueError, match="LOG_LEVEL must be one of"):
        configure_logging()
