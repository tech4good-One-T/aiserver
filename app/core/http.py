"""Request tracing and common JSON error responses."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.errors import AppError

logger = logging.getLogger(__name__)


def get_request_id(request: Request) -> str:
    """Return the server-generated identifier assigned to a request."""
    return getattr(request.state, "request_id", f"req_{uuid4().hex}")


def error_response(request: Request, error: AppError) -> JSONResponse:
    """Render the stable error envelope required by the API contract."""
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "request_id": get_request_id(request),
            }
        },
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a non-client-controlled request identifier to every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = f"req_{uuid4().hex}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def install_http_error_handling(app: FastAPI) -> None:
    """Install common middleware and exception handlers."""
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "Request rejected (request_id=%s code=%s)",
            get_request_id(request),
            exc.code,
        )
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning("Request validation failed (request_id=%s)", get_request_id(request))
        invalid_fields = {
            str(location)
            for validation_error in exc.errors()
            for location in validation_error.get("loc", ())
        }
        if "source_download_url" in invalid_fields:
            error = AppError(400, "INVALID_SOURCE_URL", "원본 다운로드 URL이 올바르지 않습니다.")
        elif "result_upload_url" in invalid_fields:
            error = AppError(400, "INVALID_RESULT_URL", "결과 업로드 URL이 올바르지 않습니다.")
        elif "result_content_type" in invalid_fields:
            error = AppError(
                422,
                "RESULT_CONTENT_TYPE_MISMATCH",
                "결과 이미지 Content-Type이 올바르지 않습니다.",
            )
        elif "polygon" in invalid_fields:
            error = AppError(422, "INVALID_REGION", "블러 영역 다각형이 올바르지 않습니다.")
        elif "selected_regions" in invalid_fields:
            error = AppError(
                400,
                "INVALID_SELECTED_REGIONS",
                "선택한 블러 영역 형식이 올바르지 않습니다.",
            )
        else:
            error = AppError(422, "INVALID_REQUEST", "요청 형식이 올바르지 않습니다.")
        return error_response(request, error)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Request failed unexpectedly (request_id=%s)", get_request_id(request))
        error = AppError(500, "INTERNAL_SERVER_ERROR", "서버 내부 오류가 발생했습니다.")
        return error_response(request, error)
