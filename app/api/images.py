"""Image analysis and protection HTTP endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

import cv2
import numpy as np
from fastapi import APIRouter, Depends, Request
from PIL import Image

from app.api.dependencies import (
    SettingsDependency,
    get_faceshield_adapter,
    get_gemini_analyzer,
    get_image_gateway,
)
from app.api.schemas import (
    AnalyzedImage,
    BoundingBox,
    DeepfakeProtectionResult,
    DeepfakeSkipReason,
    DetectionType,
    ImageAnalyzeRequest,
    ImageAnalyzeResponse,
    ImageProcessRequest,
    ImageProcessResponse,
)
from app.core.errors import AppError
from app.core.http import get_request_id
from app.services.faceshield import (
    FaceShieldAdapter,
    FaceShieldError,
    FaceShieldTimeoutError,
)
from app.services.gemini_analyzer import (
    GeminiAnalysisResult,
    GeminiAnalyzer,
    GeminiAnalyzerError,
    GeminiAnalyzerTimeoutError,
    GeminiAnalyzerUnavailableError,
)
from app.services.image_codec import DecodedImage, decode_image, encode_png
from app.services.image_gateway import ImageGateway
from app.services.image_processing import blur_regions
from app.services.risk_policy import build_analysis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/images", tags=["images"])
MAX_FACESHIELD_DELTA = 32.0

GatewayDependency = Annotated[ImageGateway, Depends(get_image_gateway)]
AnalyzerDependency = Annotated[GeminiAnalyzer, Depends(get_gemini_analyzer)]
FaceShieldDependency = Annotated[FaceShieldAdapter, Depends(get_faceshield_adapter)]


async def _analyze_for_endpoint(
    analyzer: GeminiAnalyzer,
    image_bytes: bytes,
    mime_type: str,
    *,
    processing: bool,
) -> GeminiAnalysisResult:
    try:
        return await analyzer.analyze(image_bytes, mime_type)
    except GeminiAnalyzerTimeoutError:
        if processing:
            raise AppError(
                504,
                "PROCESSING_TIMEOUT",
                "이미지 처리 시간이 초과되었습니다.",
            ) from None
        raise AppError(504, "ANALYSIS_TIMEOUT", "이미지 분석 시간이 초과되었습니다.") from None
    except GeminiAnalyzerUnavailableError:
        if processing:
            raise AppError(
                503,
                "DEEPFAKE_PROTECTION_FAILED",
                "딥페이크 방지 처리에 실패했습니다.",
            ) from None
        raise AppError(
            503,
            "ANALYSIS_MODEL_UNAVAILABLE",
            "이미지 분석 모델을 사용할 수 없습니다.",
        ) from None
    except GeminiAnalyzerError:
        if processing:
            raise AppError(
                503,
                "DEEPFAKE_PROTECTION_FAILED",
                "딥페이크 방지 처리에 실패했습니다.",
            ) from None
        raise AppError(500, "IMAGE_ANALYSIS_FAILED", "이미지 분석에 실패했습니다.") from None


def _decode_faceshield_output(protected_png: bytes, settings: SettingsDependency) -> DecodedImage:
    try:
        return decode_image(protected_png, "image/png", settings)
    except AppError:
        raise AppError(
            503,
            "DEEPFAKE_PROTECTION_FAILED",
            "딥페이크 방지 처리에 실패했습니다.",
        ) from None


@router.post("/analyze", response_model=ImageAnalyzeResponse)
async def analyze_image(
    payload: ImageAnalyzeRequest,
    request: Request,
    settings: SettingsDependency,
    gateway: GatewayDependency,
    analyzer: AnalyzerDependency,
) -> ImageAnalyzeResponse:
    """Analyze one source image without retaining it on the AI server."""
    downloaded = await gateway.download(payload.source_download_url, payload.source_object_key)
    decoded = decode_image(downloaded.data, downloaded.content_type, settings)
    raw_analysis = await _analyze_for_endpoint(
        analyzer,
        decoded.gemini_bytes,
        decoded.gemini_mime_type,
        processing=False,
    )

    try:
        risk_groups, detections = build_analysis(raw_analysis, decoded)
        response = ImageAnalyzeResponse(
            request_id=get_request_id(request),
            source_object_key=payload.source_object_key,
            image=AnalyzedImage(
                sha256=decoded.sha256,
                width=decoded.width,
                height=decoded.height,
                format=decoded.source_format,
                orientation_normalized=True,
            ),
            risk_groups=risk_groups,
            detections=detections,
        )
    except (TypeError, ValueError):
        raise AppError(500, "IMAGE_ANALYSIS_FAILED", "이미지 분석에 실패했습니다.") from None

    logger.info(
        "Image analysis completed (request_id=%s detection_count=%d)",
        get_request_id(request),
        len(detections),
    )
    return response


async def _run_faceshield(
    adapter: FaceShieldAdapter,
    normalized_png: bytes,
) -> bytes:
    try:
        return await adapter.protect_png(normalized_png)
    except FaceShieldTimeoutError:
        raise AppError(504, "PROCESSING_TIMEOUT", "이미지 처리 시간이 초과되었습니다.") from None
    except FaceShieldError:
        raise AppError(
            503,
            "DEEPFAKE_PROTECTION_FAILED",
            "딥페이크 방지 처리에 실패했습니다.",
        ) from None


def _face_crop_box(bbox: BoundingBox, image: Image.Image) -> tuple[int, int, int, int]:
    """Expand a face box with context while keeping PIL's exclusive bounds valid."""
    margin = max(16, round(max(bbox.width, bbox.height) * 0.2))
    left = max(0, bbox.x - margin)
    top = max(0, bbox.y - margin)
    right = min(image.width, bbox.x + bbox.width + margin)
    bottom = min(image.height, bbox.y + bbox.height + margin)
    return left, top, right, bottom


def _restore_faceshield_delta(
    original_crop: Image.Image,
    protected_crop: Image.Image,
) -> Image.Image:
    """Map FaceShield's perturbation back without replacing high-resolution detail."""
    original = np.asarray(original_crop.convert("RGB"), dtype=np.float32)
    protected = np.asarray(protected_crop.convert("RGB"), dtype=np.float32)
    if protected.shape[0] < 1 or protected.shape[1] < 1:
        raise AppError(
            503,
            "DEEPFAKE_PROTECTION_FAILED",
            "딥페이크 방지 처리에 실패했습니다.",
        )

    original_ratio = original.shape[1] / original.shape[0]
    protected_ratio = protected.shape[1] / protected.shape[0]
    if abs(original_ratio - protected_ratio) > max(0.02, original_ratio * 0.02):
        raise AppError(
            503,
            "DEEPFAKE_PROTECTION_FAILED",
            "딥페이크 방지 처리에 실패했습니다.",
        )

    clean_small = cv2.resize(
        original,
        (protected.shape[1], protected.shape[0]),
        interpolation=cv2.INTER_LINEAR,
    )
    delta = protected - clean_small
    if not np.isfinite(delta).all() or float(np.max(np.abs(delta))) > MAX_FACESHIELD_DELTA:
        raise AppError(
            503,
            "DEEPFAKE_PROTECTION_FAILED",
            "딥페이크 방지 처리에 실패했습니다.",
        )
    if not np.any(np.abs(delta) > 0.5):
        raise AppError(
            503,
            "DEEPFAKE_PROTECTION_FAILED",
            "딥페이크 방지 처리에 실패했습니다.",
        )
    if delta.shape[:2] != original.shape[:2]:
        delta = cv2.resize(
            delta,
            (original.shape[1], original.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )
    restored = np.clip(original + delta, 0, 255).astype(np.uint8)
    return Image.fromarray(restored, mode="RGB")


async def _protect_faces(
    image: Image.Image,
    face_boxes: list[BoundingBox],
    adapter: FaceShieldAdapter,
    settings: SettingsDependency,
) -> Image.Image:
    """Protect detected face crops and composite them without resizing the photo."""
    protected_image = image.convert("RGB").copy()
    for bbox in face_boxes:
        crop_box = _face_crop_box(bbox, protected_image)
        face_crop = protected_image.crop(crop_box)
        protected_png = await _run_faceshield(adapter, encode_png(face_crop))
        protected_crop = _decode_faceshield_output(protected_png, settings).image
        # The official CLI caps large inputs around its configured resize_shape.
        # Restore only its perturbation so the high-resolution source detail stays
        # intact; the photo and API coordinate system retain their original size.
        protected_crop = _restore_faceshield_delta(face_crop, protected_crop)
        protected_image.paste(protected_crop, crop_box[:2])
    return protected_image


async def _process_image(
    payload: ImageProcessRequest,
    request: Request,
    settings: SettingsDependency,
    gateway: ImageGateway,
    analyzer: GeminiAnalyzer,
    faceshield: FaceShieldAdapter,
) -> ImageProcessResponse:
    downloaded = await gateway.download(payload.source_download_url, payload.source_object_key)
    decoded = decode_image(downloaded.data, downloaded.content_type, settings)
    if decoded.sha256 != payload.analysis_image_sha256:
        raise AppError(
            409,
            "IMAGE_ANALYSIS_MISMATCH",
            "분석한 이미지와 처리할 이미지가 일치하지 않습니다.",
        )

    raw_analysis = await _analyze_for_endpoint(
        analyzer,
        decoded.gemini_bytes,
        decoded.gemini_mime_type,
        processing=True,
    )
    try:
        _, process_detections = build_analysis(raw_analysis, decoded)
        face_boxes = [
            detection.region.bbox
            for detection in process_detections
            if detection.type is DetectionType.FACE_EXPOSURE and detection.region is not None
        ]
    except (TypeError, ValueError):
        raise AppError(
            503,
            "DEEPFAKE_PROTECTION_FAILED",
            "딥페이크 방지 처리에 실패했습니다.",
        ) from None
    face_detected = bool(face_boxes)

    try:
        mask_result = blur_regions(
            decoded.image,
            [region.polygon for region in payload.selected_regions],
        )
        output_image = mask_result.image
        if face_detected:
            output_image = await _protect_faces(
                output_image,
                face_boxes,
                faceshield,
                settings,
            )

        final_png = encode_png(
            output_image,
            exif=None if payload.remove_metadata else decoded.preserved_exif,
        )
    except AppError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise AppError(
            500,
            "IMAGE_PROCESSING_FAILED",
            "이미지 처리에 실패했습니다.",
        ) from None

    await gateway.upload_png(payload.result_upload_url, payload.result_object_key, final_png)
    deepfake_result = (
        DeepfakeProtectionResult(attempted=True, applied=True, skip_reason=None)
        if face_detected
        else DeepfakeProtectionResult(
            attempted=True,
            applied=False,
            skip_reason=DeepfakeSkipReason.NO_FACE_DETECTED,
        )
    )

    logger.info(
        "Image processing completed "
        "(request_id=%s masked_region_count=%d deepfake_applied=%s metadata_removed=%s)",
        get_request_id(request),
        mask_result.region_count,
        face_detected,
        payload.remove_metadata,
    )
    return ImageProcessResponse(
        request_id=get_request_id(request),
        status="COMPLETED",
        source_object_key=payload.source_object_key,
        result_object_key=payload.result_object_key,
        result_content_type="image/png",
        masked_region_count=mask_result.region_count,
        deepfake_protection=deepfake_result,
        metadata_removed=payload.remove_metadata,
    )


@router.post("/process", response_model=ImageProcessResponse)
async def process_image(
    payload: ImageProcessRequest,
    request: Request,
    settings: SettingsDependency,
    gateway: GatewayDependency,
    analyzer: AnalyzerDependency,
    faceshield: FaceShieldDependency,
) -> ImageProcessResponse:
    """Mask selected regions, apply mandatory face protection, and upload PNG output."""
    try:
        async with asyncio.timeout(settings.processing_timeout_seconds):
            return await _process_image(
                payload,
                request,
                settings,
                gateway,
                analyzer,
                faceshield,
            )
    except TimeoutError:
        raise AppError(504, "PROCESSING_TIMEOUT", "이미지 처리 시간이 초과되었습니다.") from None
