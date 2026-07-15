"""FastAPI dependency factories for image API services."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.services.faceshield import FaceShieldAdapter, FaceShieldConfig
from app.services.gemini_analyzer import GeminiAnalyzer
from app.services.image_gateway import ImageGateway

SettingsDependency = Annotated[Settings, Depends(get_settings)]


def get_image_gateway(settings: SettingsDependency) -> ImageGateway:
    """Create a restricted object-storage transfer client for one request."""
    return ImageGateway(settings)


async def get_gemini_analyzer(settings: SettingsDependency) -> AsyncIterator[GeminiAnalyzer]:
    """Create a request-scoped Gemini analyzer and close its HTTP transport."""
    analyzer = GeminiAnalyzer(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.analysis_timeout_seconds,
    )
    try:
        yield analyzer
    finally:
        await analyzer.aclose()


def get_faceshield_adapter(settings: SettingsDependency) -> FaceShieldAdapter:
    """Create the adapter for the externally installed FaceShield runtime."""
    return FaceShieldAdapter(FaceShieldConfig.from_settings(settings))
