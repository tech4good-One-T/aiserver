"""Stable application errors exposed through the HTTP API."""

from dataclasses import dataclass


@dataclass(slots=True)
class AppError(Exception):
    """An expected failure with a stable client-facing code."""

    status_code: int
    code: str
    message: str

    def __str__(self) -> str:
        return self.code


def invalid_request(message: str = "요청 형식이 올바르지 않습니다.") -> AppError:
    """Return a common validation error."""
    return AppError(status_code=422, code="INVALID_REQUEST", message=message)
