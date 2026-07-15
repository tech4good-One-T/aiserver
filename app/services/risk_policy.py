"""Deterministic policy for turning model detections into API contracts.

Gemini reports what it can see and its confidence in that observation.  This
module deliberately owns the separate privacy-risk decision, API labels,
processing actions, pixel-coordinate conversion, and local EXIF GPS finding.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import ceil

from app.api.schemas import (
    BoundingBox,
    Detection,
    DetectionType,
    ImageRegion,
    ProcessingAction,
    RiskGroupCode,
    RiskGroupSummary,
    RiskLevel,
)
from app.services.gemini_analyzer import GeminiAnalysisResult, GeminiRawDetection
from app.services.image_codec import DecodedImage


@dataclass(frozen=True, slots=True)
class DetectionPolicy:
    """Immutable presentation and severity policy for one detection type."""

    label: str
    risk_level: RiskLevel


GROUP_LABELS: dict[RiskGroupCode, str] = {
    RiskGroupCode.DEEPFAKE: "딥페이크 위험",
    RiskGroupCode.IDENTITY: "신원 식별 정보",
    RiskGroupCode.VEHICLE: "차량 정보",
    RiskGroupCode.CONTACT_ACCOUNT: "연락처 및 계정 정보",
    RiskGroupCode.RESIDENCE: "주거지 정보",
    RiskGroupCode.BUILDING: "건물 식별 정보",
    RiskGroupCode.LOCATION: "위치정보 위험",
}


TYPE_POLICIES: dict[DetectionType, DetectionPolicy] = {
    DetectionType.FACE_EXPOSURE: DetectionPolicy("얼굴 노출", RiskLevel.HIGH),
    DetectionType.NATIONAL_ID_CARD: DetectionPolicy("주민등록증", RiskLevel.HIGH),
    DetectionType.DRIVERS_LICENSE: DetectionPolicy("운전면허증", RiskLevel.HIGH),
    DetectionType.PASSPORT: DetectionPolicy("여권", RiskLevel.HIGH),
    DetectionType.STUDENT_ID_CARD: DetectionPolicy("학생증", RiskLevel.MEDIUM),
    DetectionType.EMPLOYEE_ID_CARD: DetectionPolicy("사원증", RiskLevel.MEDIUM),
    DetectionType.ACCESS_BADGE: DetectionPolicy("출입증", RiskLevel.MEDIUM),
    DetectionType.PERSON_NAME: DetectionPolicy("이름", RiskLevel.LOW),
    DetectionType.DATE_OF_BIRTH: DetectionPolicy("생년월일", RiskLevel.MEDIUM),
    DetectionType.RESIDENT_REGISTRATION_NUMBER: DetectionPolicy("주민등록번호", RiskLevel.HIGH),
    DetectionType.NAME_TAG: DetectionPolicy("명찰", RiskLevel.MEDIUM),
    DetectionType.UNIFORM_REAL_NAME: DetectionPolicy("유니폼 실명", RiskLevel.MEDIUM),
    DetectionType.SHIPPING_LABEL: DetectionPolicy("택배 송장", RiskLevel.HIGH),
    DetectionType.VEHICLE_LICENSE_PLATE: DetectionPolicy("자동차 번호판", RiskLevel.HIGH),
    DetectionType.MOTORCYCLE_LICENSE_PLATE: DetectionPolicy("이륜차 번호판", RiskLevel.HIGH),
    DetectionType.PARKING_STICKER: DetectionPolicy("주차 스티커", RiskLevel.MEDIUM),
    DetectionType.VEHICLE_REGISTRATION: DetectionPolicy("차량 등록증", RiskLevel.HIGH),
    DetectionType.PARKING_PASS: DetectionPolicy("주차 정기권", RiskLevel.MEDIUM),
    DetectionType.VEHICLE_CONTACT_NUMBER: DetectionPolicy("차량 연락처", RiskLevel.HIGH),
    DetectionType.PHONE_NUMBER: DetectionPolicy("휴대전화 번호", RiskLevel.HIGH),
    DetectionType.EMAIL_ADDRESS: DetectionPolicy("이메일 주소", RiskLevel.HIGH),
    DetectionType.SNS_HANDLE: DetectionPolicy("SNS 아이디", RiskLevel.MEDIUM),
    DetectionType.BUSINESS_CARD: DetectionPolicy("명함", RiskLevel.HIGH),
    DetectionType.SCREEN_USERNAME: DetectionPolicy("화면 사용자명", RiskLevel.MEDIUM),
    DetectionType.PROFILE_INFORMATION: DetectionPolicy("프로필 정보", RiskLevel.MEDIUM),
    DetectionType.ROAD_NAME_ADDRESS: DetectionPolicy("도로명 주소", RiskLevel.HIGH),
    DetectionType.LOT_NUMBER_ADDRESS: DetectionPolicy("지번 주소", RiskLevel.HIGH),
    DetectionType.APARTMENT_UNIT: DetectionPolicy("아파트 동·호수", RiskLevel.HIGH),
    DetectionType.APARTMENT_BRAND: DetectionPolicy("아파트 브랜드명", RiskLevel.MEDIUM),
    DetectionType.BUILDING_NUMBER: DetectionPolicy("동·건물 번호", RiskLevel.MEDIUM),
    DetectionType.BUILDING_NAME: DetectionPolicy("건물명", RiskLevel.LOW),
    DetectionType.STORE_NAME: DetectionPolicy("상가명", RiskLevel.LOW),
    DetectionType.SCHOOL_NAME: DetectionPolicy("학교명", RiskLevel.MEDIUM),
    DetectionType.COMPANY_NAME: DetectionPolicy("회사명", RiskLevel.MEDIUM),
    DetectionType.EXIF_GPS: DetectionPolicy("GPS 위치정보", RiskLevel.HIGH),
    DetectionType.VISUAL_LOCATION_CLUE: DetectionPolicy("시각적 위치 단서", RiskLevel.MEDIUM),
    DetectionType.TRAVEL_ITINERARY: DetectionPolicy("여행 일정", RiskLevel.HIGH),
}

_RISK_RANK = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}


def _canonical_key(detection: GeminiRawDetection) -> tuple[object, ...]:
    """Sort model output so generated IDs do not depend on provider ordering."""
    return (
        RiskGroupCode(detection.risk_group.value).value,
        DetectionType(detection.type.value).value,
        *detection.box_2d,
        (detection.detected_text or "").strip(),
        detection.confidence,
    )


def _inclusive_pixel_interval(start: int, end: int, size: int) -> tuple[int, int]:
    """Map normalized half-open bounds to inclusive, valid pixel indices."""
    pixel_start = min(size - 1, (start * size) // 1000)
    pixel_end = min(size - 1, max(pixel_start, ceil(end * size / 1000) - 1))
    # ImageRegion requires a non-zero-area polygon. A valid normalized interval
    # can quantize to one pixel on a small image, so expand it by one valid pixel.
    if pixel_start == pixel_end and size > 1:
        if pixel_end < size - 1:
            pixel_end += 1
        else:
            pixel_start -= 1
    return pixel_start, pixel_end


def _region(raw: GeminiRawDetection, decoded: DecodedImage) -> ImageRegion:
    ymin, xmin, ymax, xmax = raw.box_2d
    x0, x1 = _inclusive_pixel_interval(xmin, xmax, decoded.width)
    y0, y1 = _inclusive_pixel_interval(ymin, ymax, decoded.height)

    return ImageRegion(
        bbox=BoundingBox(x=x0, y=y0, width=x1 - x0 + 1, height=y1 - y0 + 1),
        polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
    )


def _text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _visual_detection(
    raw: GeminiRawDetection,
    decoded: DecodedImage,
    detection_id: str,
) -> Detection:
    detection_type = DetectionType(raw.type.value)
    risk_group = RiskGroupCode(raw.risk_group.value)
    policy = TYPE_POLICIES[detection_type]
    is_face = detection_type is DetectionType.FACE_EXPOSURE
    return Detection(
        id=detection_id,
        risk_group=risk_group,
        type=detection_type,
        label=policy.label,
        confidence=raw.confidence,
        detected_text=_text_or_none(raw.detected_text),
        region=_region(raw, decoded),
        mask_supported=not is_face,
        processing_action=(
            ProcessingAction.APPLY_DEEPFAKE_PROTECTION if is_face else ProcessingAction.MASK
        ),
    )


def _exif_gps_detection() -> Detection:
    policy = TYPE_POLICIES[DetectionType.EXIF_GPS]
    return Detection(
        id="det_exif_gps_001",
        risk_group=RiskGroupCode.LOCATION,
        type=DetectionType.EXIF_GPS,
        label=policy.label,
        confidence=1.0,
        detected_text=None,
        region=None,
        mask_supported=False,
        processing_action=ProcessingAction.REMOVE_METADATA,
    )


def _summaries(detections: list[Detection]) -> list[RiskGroupSummary]:
    grouped: dict[RiskGroupCode, list[Detection]] = defaultdict(list)
    for detection in detections:
        grouped[detection.risk_group].append(detection)

    summaries: list[RiskGroupSummary] = []
    for code in RiskGroupCode:
        group_detections = grouped[code]
        risk_level = max(
            (TYPE_POLICIES[detection.type].risk_level for detection in group_detections),
            key=_RISK_RANK.__getitem__,
            default=RiskLevel.LOW,
        )
        detection_ids = [detection.id for detection in group_detections]
        summaries.append(
            RiskGroupSummary(
                code=code,
                label=GROUP_LABELS[code],
                detected=bool(group_detections),
                risk_level=risk_level,
                detection_count=len(group_detections),
                detection_ids=detection_ids,
            )
        )
    return summaries


def build_analysis(
    raw: GeminiAnalysisResult,
    decoded: DecodedImage,
) -> tuple[list[RiskGroupSummary], list[Detection]]:
    """Build complete risk groups and API detections from trusted model output.

    Coordinates refer to ``decoded.image``, which has already had its EXIF
    orientation applied.  EXIF GPS is derived locally and its value is never
    returned or passed to Gemini.
    """
    type_counts: dict[DetectionType, int] = defaultdict(int)
    detections: list[Detection] = []
    for raw_detection in sorted(raw.detections, key=_canonical_key):
        detection_type = DetectionType(raw_detection.type.value)
        type_counts[detection_type] += 1
        detection_id = f"det_{detection_type.value.lower()}_{type_counts[detection_type]:03d}"
        detections.append(_visual_detection(raw_detection, decoded, detection_id))

    if decoded.has_gps:
        detections.append(_exif_gps_detection())

    return _summaries(detections), detections


__all__ = ["GROUP_LABELS", "TYPE_POLICIES", "DetectionPolicy", "build_analysis"]
