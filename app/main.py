"""FastAPI application entrypoint."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.images import router as images_router
from app.core.http import install_http_error_handling
from app.core.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Log application lifecycle events."""
    app_env = os.getenv("APP_ENV", "local")
    logger.info("Application started (environment=%s)", app_env)
    yield
    logger.info("Application stopped")


app = FastAPI(title="aiserver", lifespan=lifespan)
install_http_error_handling(app)
app.include_router(images_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return the process health status."""
    return {"status": "ok"}
