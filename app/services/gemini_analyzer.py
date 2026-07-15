"""Gemini-backed visual privacy-risk detection.

The service accepts metadata-free orientation-normalized PNG, JPEG, or WebP transport
bytes. It deliberately returns
model detections in Gemini's normalized 0..1000 coordinate space; converting them to
pixel coordinates and assigning policy risk levels belong to the API orchestration
layer.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from contextlib import suppress
from enum import StrEnum
from math import isfinite
from types import ModuleType
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_TIMEOUT_SECONDS = 60.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
WEBP_SIGNATURE_PREFIX = b"RIFF"
NormalizedCoordinate = Annotated[int, Field(strict=True, ge=0, le=1000)]


class GeminiRiskGroup(StrEnum):
    """Risk groups that Gemini may derive from visible pixels."""

    DEEPFAKE = "DEEPFAKE"
    IDENTITY = "IDENTITY"
    VEHICLE = "VEHICLE"
    CONTACT_ACCOUNT = "CONTACT_ACCOUNT"
    RESIDENCE = "RESIDENCE"
    BUILDING = "BUILDING"
    LOCATION = "LOCATION"


class GeminiDetectionType(StrEnum):
    """Allowlisted visual risk types from the analyze API contract.

    EXIF_GPS is intentionally absent: normalized PNG bytes do not contain the source
    metadata, so GPS metadata must be detected before normalization by the image codec.
    """

    FACE_EXPOSURE = "FACE_EXPOSURE"

    NATIONAL_ID_CARD = "NATIONAL_ID_CARD"
    DRIVERS_LICENSE = "DRIVERS_LICENSE"
    PASSPORT = "PASSPORT"
    STUDENT_ID_CARD = "STUDENT_ID_CARD"
    EMPLOYEE_ID_CARD = "EMPLOYEE_ID_CARD"
    ACCESS_BADGE = "ACCESS_BADGE"
    PERSON_NAME = "PERSON_NAME"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    RESIDENT_REGISTRATION_NUMBER = "RESIDENT_REGISTRATION_NUMBER"
    NAME_TAG = "NAME_TAG"
    UNIFORM_REAL_NAME = "UNIFORM_REAL_NAME"
    SHIPPING_LABEL = "SHIPPING_LABEL"

    VEHICLE_LICENSE_PLATE = "VEHICLE_LICENSE_PLATE"
    MOTORCYCLE_LICENSE_PLATE = "MOTORCYCLE_LICENSE_PLATE"
    PARKING_STICKER = "PARKING_STICKER"
    VEHICLE_REGISTRATION = "VEHICLE_REGISTRATION"
    PARKING_PASS = "PARKING_PASS"
    VEHICLE_CONTACT_NUMBER = "VEHICLE_CONTACT_NUMBER"

    PHONE_NUMBER = "PHONE_NUMBER"
    EMAIL_ADDRESS = "EMAIL_ADDRESS"
    SNS_HANDLE = "SNS_HANDLE"
    BUSINESS_CARD = "BUSINESS_CARD"
    SCREEN_USERNAME = "SCREEN_USERNAME"
    PROFILE_INFORMATION = "PROFILE_INFORMATION"

    ROAD_NAME_ADDRESS = "ROAD_NAME_ADDRESS"
    LOT_NUMBER_ADDRESS = "LOT_NUMBER_ADDRESS"
    APARTMENT_UNIT = "APARTMENT_UNIT"

    APARTMENT_BRAND = "APARTMENT_BRAND"
    BUILDING_NUMBER = "BUILDING_NUMBER"
    BUILDING_NAME = "BUILDING_NAME"
    STORE_NAME = "STORE_NAME"
    SCHOOL_NAME = "SCHOOL_NAME"
    COMPANY_NAME = "COMPANY_NAME"

    VISUAL_LOCATION_CLUE = "VISUAL_LOCATION_CLUE"
    TRAVEL_ITINERARY = "TRAVEL_ITINERARY"


_TYPE_GROUPS: dict[GeminiDetectionType, GeminiRiskGroup] = {
    GeminiDetectionType.FACE_EXPOSURE: GeminiRiskGroup.DEEPFAKE,
    GeminiDetectionType.NATIONAL_ID_CARD: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.DRIVERS_LICENSE: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.PASSPORT: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.STUDENT_ID_CARD: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.EMPLOYEE_ID_CARD: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.ACCESS_BADGE: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.PERSON_NAME: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.DATE_OF_BIRTH: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.RESIDENT_REGISTRATION_NUMBER: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.NAME_TAG: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.UNIFORM_REAL_NAME: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.SHIPPING_LABEL: GeminiRiskGroup.IDENTITY,
    GeminiDetectionType.VEHICLE_LICENSE_PLATE: GeminiRiskGroup.VEHICLE,
    GeminiDetectionType.MOTORCYCLE_LICENSE_PLATE: GeminiRiskGroup.VEHICLE,
    GeminiDetectionType.PARKING_STICKER: GeminiRiskGroup.VEHICLE,
    GeminiDetectionType.VEHICLE_REGISTRATION: GeminiRiskGroup.VEHICLE,
    GeminiDetectionType.PARKING_PASS: GeminiRiskGroup.VEHICLE,
    GeminiDetectionType.VEHICLE_CONTACT_NUMBER: GeminiRiskGroup.VEHICLE,
    GeminiDetectionType.PHONE_NUMBER: GeminiRiskGroup.CONTACT_ACCOUNT,
    GeminiDetectionType.EMAIL_ADDRESS: GeminiRiskGroup.CONTACT_ACCOUNT,
    GeminiDetectionType.SNS_HANDLE: GeminiRiskGroup.CONTACT_ACCOUNT,
    GeminiDetectionType.BUSINESS_CARD: GeminiRiskGroup.CONTACT_ACCOUNT,
    GeminiDetectionType.SCREEN_USERNAME: GeminiRiskGroup.CONTACT_ACCOUNT,
    GeminiDetectionType.PROFILE_INFORMATION: GeminiRiskGroup.CONTACT_ACCOUNT,
    GeminiDetectionType.ROAD_NAME_ADDRESS: GeminiRiskGroup.RESIDENCE,
    GeminiDetectionType.LOT_NUMBER_ADDRESS: GeminiRiskGroup.RESIDENCE,
    GeminiDetectionType.APARTMENT_UNIT: GeminiRiskGroup.RESIDENCE,
    GeminiDetectionType.APARTMENT_BRAND: GeminiRiskGroup.BUILDING,
    GeminiDetectionType.BUILDING_NUMBER: GeminiRiskGroup.BUILDING,
    GeminiDetectionType.BUILDING_NAME: GeminiRiskGroup.BUILDING,
    GeminiDetectionType.STORE_NAME: GeminiRiskGroup.BUILDING,
    GeminiDetectionType.SCHOOL_NAME: GeminiRiskGroup.BUILDING,
    GeminiDetectionType.COMPANY_NAME: GeminiRiskGroup.BUILDING,
    GeminiDetectionType.VISUAL_LOCATION_CLUE: GeminiRiskGroup.LOCATION,
    GeminiDetectionType.TRAVEL_ITINERARY: GeminiRiskGroup.LOCATION,
}


class GeminiRawDetection(BaseModel):
    """One validated detection in normalized Gemini coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    risk_group: GeminiRiskGroup
    type: GeminiDetectionType
    confidence: float = Field(strict=True, ge=0.0, le=1.0)
    detected_text: str | None = Field(default=None, max_length=512)
    box_2d: list[NormalizedCoordinate] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Bounding box [ymin, xmin, ymax, xmax], with each integer normalized "
            "to the inclusive range 0..1000."
        ),
    )

    @model_validator(mode="after")
    def validate_detection(self) -> GeminiRawDetection:
        """Reject invalid boxes and mismatched type/group combinations."""
        ymin, xmin, ymax, xmax = self.box_2d
        if ymin >= ymax or xmin >= xmax:
            raise ValueError("box_2d must have positive width and height")

        expected_group = _TYPE_GROUPS[self.type]
        if self.risk_group is not expected_group:
            raise ValueError("detection type does not belong to risk_group")
        return self


class GeminiAnalysisResult(BaseModel):
    """Validated raw detections returned by Gemini."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    detections: list[GeminiRawDetection] = Field(default_factory=list, max_length=100)


class GeminiAnalyzerError(Exception):
    """Base class for sanitized Gemini analysis failures."""


class GeminiAnalyzerUnavailableError(GeminiAnalyzerError):
    """Gemini is not configured, installed, or reachable."""


class GeminiAnalyzerTimeoutError(GeminiAnalyzerError):
    """Gemini did not return within the configured deadline."""


class GeminiAnalyzerInvalidResponseError(GeminiAnalyzerError):
    """Gemini returned data outside the strict analysis contract."""


class GeminiAnalyzerInputError(GeminiAnalyzerError):
    """The provider received unsupported image transport bytes."""


_PROMPT = """
Analyze the supplied image for privacy and deepfake-abuse risks that are visibly present.
Return every distinct detection and no explanatory prose.

Rules:
- The top-level JSON value must be an object with exactly one key named detections.
  Never return a bare array. For no findings, return {"detections": []}.
- Every item in detections must contain exactly risk_group, type, confidence,
  detected_text, and box_2d.
- Use only the risk_group and type enum values in the response schema.
- box_2d is exactly [ymin, xmin, ymax, xmax], using integer coordinates normalized to
  0..1000. The box must tightly enclose the complete sensitive item and have positive area.
- For a face, return FACE_EXPOSURE around the face, not the whole person.
- For a document, card, screen, plate, sticker, badge, label, or itinerary, enclose the
  complete sensitive object. Also emit narrower text detections only when they add a
  different documented risk type.
- detected_text contains only text that is visibly readable inside the box. Use null for
  non-text objects or unreadable text. Never guess missing characters or a person's identity.
- confidence is visual detection/classification confidence from 0 to 1, not privacy severity.
- Do not report generic people, cars, scenery, or ordinary objects unless they match a
  documented risk type.
- Do not return EXIF_GPS. Metadata has already been removed and is inspected separately.
- Return an empty detections array if no listed risk is visible.

Type-to-group mapping:
DEEPFAKE: FACE_EXPOSURE
IDENTITY: NATIONAL_ID_CARD, DRIVERS_LICENSE, PASSPORT, STUDENT_ID_CARD,
EMPLOYEE_ID_CARD, ACCESS_BADGE, PERSON_NAME, DATE_OF_BIRTH,
RESIDENT_REGISTRATION_NUMBER, NAME_TAG, UNIFORM_REAL_NAME, SHIPPING_LABEL
VEHICLE: VEHICLE_LICENSE_PLATE, MOTORCYCLE_LICENSE_PLATE, PARKING_STICKER,
VEHICLE_REGISTRATION, PARKING_PASS, VEHICLE_CONTACT_NUMBER
CONTACT_ACCOUNT: PHONE_NUMBER, EMAIL_ADDRESS, SNS_HANDLE, BUSINESS_CARD,
SCREEN_USERNAME, PROFILE_INFORMATION
RESIDENCE: ROAD_NAME_ADDRESS, LOT_NUMBER_ADDRESS, APARTMENT_UNIT
BUILDING: APARTMENT_BRAND, BUILDING_NUMBER, BUILDING_NAME, STORE_NAME,
SCHOOL_NAME, COMPANY_NAME
LOCATION: VISUAL_LOCATION_CLUE, TRAVEL_ITINERARY
""".strip()


def _load_google_sdk() -> tuple[ModuleType, ModuleType]:
    """Import the optional SDK only when a Gemini request is made."""
    try:
        genai = importlib.import_module("google.genai")
        types = importlib.import_module("google.genai.types")
    except (ImportError, ModuleNotFoundError):
        raise GeminiAnalyzerUnavailableError("Gemini SDK is unavailable") from None
    return genai, types


def _timeout_from_environment() -> float:
    raw_value = os.getenv("GEMINI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        value = float(raw_value)
    except ValueError:
        raise GeminiAnalyzerUnavailableError("Gemini timeout configuration is invalid") from None
    if value <= 0:
        raise GeminiAnalyzerUnavailableError("Gemini timeout configuration is invalid")
    return value


def _looks_like_timeout(exc: Exception) -> bool:
    """Recognize common SDK transport timeout classes without importing transports."""
    class_name = type(exc).__name__.lower()
    return isinstance(exc, TimeoutError) or "timeout" in class_name or "timedout" in class_name


class GeminiAnalyzer:
    """Analyze metadata-free image transport bytes using Gemini structured output."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._configured_api_key = api_key.strip() if api_key else None
        self.model = (model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)).strip()
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else _timeout_from_environment()
        )
        if not self.model or not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise GeminiAnalyzerUnavailableError("Gemini configuration is invalid")

        self._client: Any | None = None
        self._types: ModuleType | None = None

    async def analyze(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
    ) -> GeminiAnalysisResult:
        """Return strict raw detections for a metadata-free image transport."""
        valid_signature = isinstance(image_bytes, bytes) and (
            (mime_type == "image/png" and image_bytes.startswith(PNG_SIGNATURE))
            or (mime_type == "image/jpeg" and image_bytes.startswith(JPEG_SIGNATURE))
            or (
                mime_type == "image/webp"
                and image_bytes.startswith(WEBP_SIGNATURE_PREFIX)
                and image_bytes[8:12] == b"WEBP"
            )
        )
        if not valid_signature:
            raise GeminiAnalyzerInputError("Gemini analyzer requires supported image bytes")

        client, types = self._get_client()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        )

        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=self.model,
                    contents=[image_part, _PROMPT],
                    config=config,
                ),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            raise GeminiAnalyzerTimeoutError("Gemini analysis timed out") from None
        except Exception as exc:
            if _looks_like_timeout(exc):
                raise GeminiAnalyzerTimeoutError("Gemini analysis timed out") from None
            raise GeminiAnalyzerUnavailableError("Gemini analysis is unavailable") from None

        return self._validate_response(response)

    async def aclose(self) -> None:
        """Close the lazily created async transport, if a request created one."""
        client = self._client
        self._client = None
        self._types = None
        if client is None:
            return
        async_client = getattr(client, "aio", None)
        close = getattr(async_client, "aclose", None)
        if close is not None:
            with suppress(Exception):
                await close()

    def _get_client(self) -> tuple[Any, ModuleType]:
        if self._client is not None and self._types is not None:
            return self._client, self._types

        api_key = self._configured_api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise GeminiAnalyzerUnavailableError("Gemini API key is not configured")

        genai, types = _load_google_sdk()
        try:
            client = genai.Client(
                api_key=api_key,
                http_options={"timeout": int(self.timeout_seconds * 1000)},
            )
        except Exception:
            raise GeminiAnalyzerUnavailableError("Gemini client initialization failed") from None

        self._client = client
        self._types = types
        return client, types

    @staticmethod
    def _validate_response(response: Any) -> GeminiAnalysisResult:
        try:
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, GeminiAnalysisResult):
                return parsed
            if parsed is not None:
                return GeminiAnalysisResult.model_validate(parsed)

            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text:
                raise ValueError("missing structured response")
            return GeminiAnalysisResult.model_validate_json(text)
        except (TypeError, ValueError, ValidationError):
            raise GeminiAnalyzerInvalidResponseError(
                "Gemini returned an invalid analysis response"
            ) from None


__all__ = [
    "GeminiAnalysisResult",
    "GeminiAnalyzer",
    "GeminiAnalyzerError",
    "GeminiAnalyzerInputError",
    "GeminiAnalyzerInvalidResponseError",
    "GeminiAnalyzerTimeoutError",
    "GeminiAnalyzerUnavailableError",
    "GeminiDetectionType",
    "GeminiRawDetection",
    "GeminiRiskGroup",
]
