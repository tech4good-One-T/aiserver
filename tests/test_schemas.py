from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    ErrorResponse,
    ImageAnalyzeRequest,
    ImageAnalyzeResponse,
    ImageProcessRequest,
    ImageProcessResponse,
    RiskGroupCode,
    SelectedRegion,
)

SHA256 = "a" * 64


def _risk_groups() -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for code in RiskGroupCode:
        detected = code is RiskGroupCode.VEHICLE
        groups.append(
            {
                "code": code.value,
                "label": code.value,
                "detected": detected,
                "risk_level": "HIGH" if detected else "LOW",
                "detection_count": 1 if detected else 0,
                "detection_ids": ["det_plate_001"] if detected else [],
            }
        )
    return groups


def _analyze_response_payload() -> dict[str, object]:
    return {
        "request_id": "req_01JZ123ABC",
        "source_object_key": "original/2026/07/image-123.jpg",
        "image": {
            "sha256": SHA256.upper(),
            "width": 1920,
            "height": 1080,
            "format": "jpeg",
            "orientation_normalized": True,
        },
        "risk_groups": _risk_groups(),
        "detections": [
            {
                "id": "det_plate_001",
                "risk_group": "VEHICLE",
                "type": "VEHICLE_LICENSE_PLATE",
                "label": "자동차 번호판",
                "confidence": 0.94,
                "detected_text": "12가3456",
                "region": {
                    "bbox": {"x": 820, "y": 750, "width": 280, "height": 90},
                    "polygon": [[820, 750], [1100, 750], [1100, 840], [820, 840]],
                },
                "mask_supported": True,
                "processing_action": "MASK",
            }
        ],
    }


def _process_request_payload() -> dict[str, object]:
    return {
        "source_object_key": "original/2026/07/image-123.jpg",
        "source_download_url": "https://bucket.example/original/image-123.jpg?signature=abc",
        "result_object_key": "protected/2026/07/image-123.png",
        "result_upload_url": "https://bucket.example/protected/image-123.png?signature=def",
        "result_content_type": "image/png",
        "analysis_image_sha256": SHA256.upper(),
        "selected_regions": [
            {
                "detection_id": "det_plate_001",
                "risk_group": "VEHICLE",
                "polygon": [[820, 750], [1100, 750], [1100, 840], [820, 840]],
            }
        ],
        "remove_metadata": True,
    }


def test_analyze_request_accepts_https_presigned_url() -> None:
    request = ImageAnalyzeRequest.model_validate(
        {
            "source_object_key": "original/image.jpg",
            "source_download_url": "https://bucket.example/original/image.jpg?signature=abc",
        }
    )

    assert request.source_object_key == "original/image.jpg"
    assert request.source_download_url.startswith("https://")


@pytest.mark.parametrize(
    "source_download_url",
    [
        "http://bucket.example/image.jpg?signature=abc",
        "https:///image.jpg?signature=abc",
        "https://user:password@bucket.example/image.jpg",
        "https://bucket.example/image.jpg#fragment",
    ],
)
def test_analyze_request_rejects_unsafe_url(source_download_url: str) -> None:
    with pytest.raises(ValidationError):
        ImageAnalyzeRequest.model_validate(
            {
                "source_object_key": "original/image.jpg",
                "source_download_url": source_download_url,
            }
        )


def test_models_reject_undocumented_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ImageAnalyzeRequest.model_validate(
            {
                "source_object_key": "original/image.jpg",
                "source_download_url": "https://bucket.example/image.jpg",
                "unexpected": True,
            }
        )


def test_analyze_response_validates_complete_contract() -> None:
    response = ImageAnalyzeResponse.model_validate(_analyze_response_payload())

    assert response.image.sha256 == SHA256
    assert len(response.risk_groups) == 7
    assert response.detections[0].risk_group is RiskGroupCode.VEHICLE
    assert response.model_dump(mode="json")["detections"][0]["region"]["polygon"] == [
        [820, 750],
        [1100, 750],
        [1100, 840],
        [820, 840],
    ]


def test_analyze_response_rejects_invalid_sha256() -> None:
    payload = _analyze_response_payload()
    image = payload["image"]
    assert isinstance(image, dict)
    image["sha256"] = "abc123"

    with pytest.raises(ValidationError):
        ImageAnalyzeResponse.model_validate(payload)


def test_analyze_response_requires_all_risk_groups_once() -> None:
    payload = _analyze_response_payload()
    risk_groups = payload["risk_groups"]
    assert isinstance(risk_groups, list)
    risk_groups[-1] = deepcopy(risk_groups[0])

    with pytest.raises(ValidationError, match="every risk group exactly once"):
        ImageAnalyzeResponse.model_validate(payload)


def test_analyze_response_rejects_inconsistent_detection_reference() -> None:
    payload = _analyze_response_payload()
    risk_groups = payload["risk_groups"]
    assert isinstance(risk_groups, list)
    vehicle_group = next(group for group in risk_groups if group["code"] == "VEHICLE")
    vehicle_group["detection_ids"] = ["det_unknown"]

    with pytest.raises(ValidationError, match="unknown detection id"):
        ImageAnalyzeResponse.model_validate(payload)


def test_analyze_response_rejects_region_outside_actual_image() -> None:
    payload = _analyze_response_payload()
    image = payload["image"]
    assert isinstance(image, dict)
    image["width"] = 1000

    with pytest.raises(ValidationError, match="outside the image"):
        ImageAnalyzeResponse.model_validate(payload)


def test_process_request_accepts_selected_regions_and_normalizes_sha256() -> None:
    request = ImageProcessRequest.model_validate(_process_request_payload())

    assert request.analysis_image_sha256 == SHA256
    assert request.result_content_type == "image/png"
    assert len(request.selected_regions) == 1


def test_process_request_accepts_no_selected_regions() -> None:
    payload = _process_request_payload()
    payload["selected_regions"] = []

    request = ImageProcessRequest.model_validate(payload)

    assert request.selected_regions == []


def test_process_request_requires_png_result() -> None:
    payload = _process_request_payload()
    payload["result_content_type"] = "image/jpeg"

    with pytest.raises(ValidationError):
        ImageProcessRequest.model_validate(payload)


def test_process_request_uses_strict_boolean() -> None:
    payload = _process_request_payload()
    payload["remove_metadata"] = "true"

    with pytest.raises(ValidationError):
        ImageProcessRequest.model_validate(payload)


def test_process_request_rejects_duplicate_detection_ids() -> None:
    payload = _process_request_payload()
    selected_regions = payload["selected_regions"]
    assert isinstance(selected_regions, list)
    selected_regions.append(deepcopy(selected_regions[0]))

    with pytest.raises(ValidationError, match="detection_ids must be unique"):
        ImageProcessRequest.model_validate(payload)


@pytest.mark.parametrize(
    "polygon",
    [
        [[0, 0], [10, 0]],
        [[0, 0], [10, 0], [-1, 10]],
        [[0, 0], [10, 0], [4097, 10]],
        [[0, 0], [10, 10], [0, 10], [10, 0]],
        [[0, 0], [10, 0], [20, 0]],
    ],
)
def test_selected_region_rejects_invalid_polygon(polygon: list[list[int]]) -> None:
    with pytest.raises(ValidationError):
        SelectedRegion.model_validate(
            {
                "detection_id": "det_001",
                "risk_group": "IDENTITY",
                "polygon": polygon,
            }
        )


def test_selected_region_rejects_coerced_coordinate() -> None:
    with pytest.raises(ValidationError):
        SelectedRegion.model_validate(
            {
                "detection_id": "det_001",
                "risk_group": "IDENTITY",
                "polygon": [["0", 0], [10, 0], [0, 10]],
            }
        )


def test_process_request_rejects_unbounded_region_input() -> None:
    payload = _process_request_payload()
    selected_regions = payload["selected_regions"]
    assert isinstance(selected_regions, list)
    selected_regions[0]["polygon"] = [[index, 0] for index in range(129)]

    with pytest.raises(ValidationError):
        ImageProcessRequest.model_validate(payload)


def test_process_request_rejects_too_many_selected_regions() -> None:
    payload = _process_request_payload()
    selected_regions = payload["selected_regions"]
    assert isinstance(selected_regions, list)
    selected_regions.extend(
        {
            "detection_id": f"det_{index:03d}",
            "risk_group": "IDENTITY",
            "polygon": [[0, 0], [10, 0], [0, 10]],
        }
        for index in range(100)
    )

    with pytest.raises(ValidationError):
        ImageProcessRequest.model_validate(payload)


def test_selected_region_rejects_deepfake_mask() -> None:
    with pytest.raises(ValidationError, match="protected automatically"):
        SelectedRegion.model_validate(
            {
                "detection_id": "det_face_001",
                "risk_group": "DEEPFAKE",
                "polygon": [[0, 0], [10, 0], [0, 10]],
            }
        )


@pytest.mark.parametrize(
    ("applied", "skip_reason"),
    [(True, None), (False, "NO_FACE_DETECTED")],
)
def test_process_response_validates_deepfake_outcomes(
    applied: bool, skip_reason: str | None
) -> None:
    response = ImageProcessResponse.model_validate(
        {
            "request_id": "req_01JZ123ABC",
            "status": "COMPLETED",
            "source_object_key": "original/image.jpg",
            "result_object_key": "protected/image.png",
            "result_content_type": "image/png",
            "masked_region_count": 1,
            "deepfake_protection": {
                "attempted": True,
                "applied": applied,
                "skip_reason": skip_reason,
            },
            "metadata_removed": True,
        }
    )

    assert response.deepfake_protection.applied is applied


def test_process_response_rejects_ambiguous_deepfake_outcome() -> None:
    with pytest.raises(ValidationError):
        ImageProcessResponse.model_validate(
            {
                "request_id": "req_01JZ123ABC",
                "status": "COMPLETED",
                "source_object_key": "original/image.jpg",
                "result_object_key": "protected/image.png",
                "result_content_type": "image/png",
                "masked_region_count": 0,
                "deepfake_protection": {
                    "attempted": True,
                    "applied": False,
                    "skip_reason": None,
                },
                "metadata_removed": False,
            }
        )


def test_common_error_response_matches_documented_shape() -> None:
    response = ErrorResponse.model_validate(
        {
            "error": {
                "code": "UNSUPPORTED_IMAGE_FORMAT",
                "message": "지원하지 않는 이미지 형식입니다.",
                "request_id": "req_01JZ123ABC",
            }
        }
    )

    assert response.error.code.value == "UNSUPPORTED_IMAGE_FORMAT"
