from PIL import Image

from app.api.schemas import (
    DetectionType,
    ProcessingAction,
    RiskGroupCode,
    RiskLevel,
)
from app.services.gemini_analyzer import GeminiAnalysisResult, GeminiRawDetection
from app.services.image_codec import DecodedImage, encode_png
from app.services.risk_policy import GROUP_LABELS, TYPE_POLICIES, build_analysis


def _decoded(*, width: int = 1200, height: int = 800, has_gps: bool = False) -> DecodedImage:
    image = Image.new("RGB", (width, height), "white")
    normalized_png = encode_png(image)
    return DecodedImage(
        image=image,
        source_format="jpeg",
        sha256="a" * 64,
        normalized_png=normalized_png,
        has_gps=has_gps,
        preserved_exif=None,
        metadata_fingerprint=b"image-metadata-v1\x00",
        gemini_bytes=normalized_png,
        gemini_mime_type="image/png",
    )


def _raw(
    detection_type: str,
    risk_group: str,
    *,
    box: list[int] | None = None,
    confidence: float = 0.9,
    text: str | None = None,
) -> GeminiRawDetection:
    return GeminiRawDetection.model_validate(
        {
            "risk_group": risk_group,
            "type": detection_type,
            "confidence": confidence,
            "detected_text": text,
            "box_2d": box or [125, 250, 875, 750],
        }
    )


def test_policy_tables_cover_every_contract_enum() -> None:
    assert set(TYPE_POLICIES) == set(DetectionType)
    assert set(GROUP_LABELS) == set(RiskGroupCode)


def test_no_detections_still_returns_all_seven_low_risk_groups() -> None:
    groups, detections = build_analysis(GeminiAnalysisResult(), _decoded())

    assert detections == []
    assert [group.code for group in groups] == list(RiskGroupCode)
    assert all(not group.detected for group in groups)
    assert all(group.risk_level is RiskLevel.LOW for group in groups)
    assert all(group.detection_count == 0 for group in groups)


def test_normalized_box_becomes_inclusive_pixel_region() -> None:
    analysis = GeminiAnalysisResult(detections=[_raw("VEHICLE_LICENSE_PLATE", "VEHICLE")])

    _, detections = build_analysis(analysis, _decoded())

    region = detections[0].region
    assert region is not None
    assert region.bbox.model_dump() == {"x": 300, "y": 100, "width": 600, "height": 600}
    assert region.polygon == [(300, 100), (899, 100), (899, 699), (300, 699)]


def test_full_normalized_box_never_uses_coordinate_equal_to_dimension() -> None:
    analysis = GeminiAnalysisResult(
        detections=[_raw("BUSINESS_CARD", "CONTACT_ACCOUNT", box=[0, 0, 1000, 1000])]
    )

    _, detections = build_analysis(analysis, _decoded(width=2, height=2))

    region = detections[0].region
    assert region is not None
    assert region.bbox.model_dump() == {"x": 0, "y": 0, "width": 2, "height": 2}
    assert region.polygon == [(0, 0), (1, 0), (1, 1), (0, 1)]


def test_single_pixel_quantization_expands_inside_image_bounds() -> None:
    analysis = GeminiAnalysisResult(
        detections=[_raw("BUSINESS_CARD", "CONTACT_ACCOUNT", box=[999, 999, 1000, 1000])]
    )

    _, detections = build_analysis(analysis, _decoded(width=100, height=100))

    region = detections[0].region
    assert region is not None
    assert region.bbox.model_dump() == {"x": 98, "y": 98, "width": 2, "height": 2}
    assert region.polygon == [(98, 98), (99, 98), (99, 99), (98, 99)]


def test_face_uses_mandatory_deepfake_action_and_high_risk() -> None:
    analysis = GeminiAnalysisResult(detections=[_raw("FACE_EXPOSURE", "DEEPFAKE")])

    groups, detections = build_analysis(analysis, _decoded())

    face = detections[0]
    assert face.mask_supported is False
    assert face.processing_action is ProcessingAction.APPLY_DEEPFAKE_PROTECTION
    deepfake_group = next(group for group in groups if group.code is RiskGroupCode.DEEPFAKE)
    assert deepfake_group.risk_level is RiskLevel.HIGH


def test_visual_privacy_detection_is_maskable_and_strips_text() -> None:
    analysis = GeminiAnalysisResult(detections=[_raw("PERSON_NAME", "IDENTITY", text="  홍길동  ")])

    groups, detections = build_analysis(analysis, _decoded())

    assert detections[0].detected_text == "홍길동"
    assert detections[0].mask_supported is True
    assert detections[0].processing_action is ProcessingAction.MASK
    identity_group = next(group for group in groups if group.code is RiskGroupCode.IDENTITY)
    assert identity_group.risk_level is RiskLevel.LOW


def test_group_risk_uses_highest_detection_policy_level() -> None:
    analysis = GeminiAnalysisResult(
        detections=[
            _raw("PERSON_NAME", "IDENTITY"),
            _raw("STUDENT_ID_CARD", "IDENTITY", box=[0, 0, 100, 100]),
            _raw("PASSPORT", "IDENTITY", box=[200, 200, 300, 300]),
        ]
    )

    groups, _ = build_analysis(analysis, _decoded())

    identity_group = next(group for group in groups if group.code is RiskGroupCode.IDENTITY)
    assert identity_group.risk_level is RiskLevel.HIGH
    assert identity_group.detection_count == 3


def test_exif_gps_is_added_locally_without_exposing_coordinates() -> None:
    groups, detections = build_analysis(GeminiAnalysisResult(), _decoded(has_gps=True))

    assert len(detections) == 1
    gps = detections[0]
    assert gps.id == "det_exif_gps_001"
    assert gps.type is DetectionType.EXIF_GPS
    assert gps.region is None
    assert gps.detected_text is None
    assert gps.confidence == 1.0
    assert gps.processing_action is ProcessingAction.REMOVE_METADATA
    location = next(group for group in groups if group.code is RiskGroupCode.LOCATION)
    assert location.risk_level is RiskLevel.HIGH
    assert location.detection_ids == ["det_exif_gps_001"]


def test_ids_and_output_order_are_stable_when_provider_order_changes() -> None:
    name_left = _raw("PERSON_NAME", "IDENTITY", box=[0, 0, 100, 100], text="가")
    name_right = _raw("PERSON_NAME", "IDENTITY", box=[0, 800, 100, 900], text="나")
    plate = _raw("VEHICLE_LICENSE_PLATE", "VEHICLE")

    _, forward = build_analysis(
        GeminiAnalysisResult(detections=[name_right, plate, name_left]), _decoded()
    )
    _, reverse = build_analysis(
        GeminiAnalysisResult(detections=[name_left, plate, name_right]), _decoded()
    )

    assert [item.model_dump() for item in forward] == [item.model_dump() for item in reverse]
    assert [item.id for item in forward] == [
        "det_person_name_001",
        "det_person_name_002",
        "det_vehicle_license_plate_001",
    ]
