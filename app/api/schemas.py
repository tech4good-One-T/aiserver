"""Pydantic contracts shared by the image analysis and processing endpoints."""

from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_IMAGE_DIMENSION = 4096
MAX_REGION_POINTS = 128
MAX_SELECTED_REGIONS = 100


def _validate_https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("must be a valid HTTPS URL") from exc

    if parsed.scheme.lower() != "https" or not hostname:
        raise ValueError("must be an HTTPS URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("must not contain user information")
    if parsed.fragment:
        raise ValueError("must not contain a fragment")
    return value


def _normalize_sha256(value: str) -> str:
    return value.lower()


NonEmptyString = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4096),
]
ObjectKey = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
]
RequestId = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
DetectionId = Annotated[
    StrictStr,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    ),
]
HttpsUrl = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=8192),
    AfterValidator(_validate_https_url),
]
Sha256Hex = Annotated[
    StrictStr,
    StringConstraints(pattern=r"^[0-9a-fA-F]{64}$"),
    AfterValidator(_normalize_sha256),
]
PixelCoordinate = Annotated[int, Field(strict=True, ge=0, le=MAX_IMAGE_DIMENSION)]
PositiveDimension = Annotated[int, Field(strict=True, ge=1, le=MAX_IMAGE_DIMENSION)]
NonNegativeCount = Annotated[int, Field(strict=True, ge=0)]
Confidence = Annotated[float, Field(strict=True, ge=0.0, le=1.0)]
Point = tuple[PixelCoordinate, PixelCoordinate]
Polygon = Annotated[list[Point], Field(min_length=3, max_length=MAX_REGION_POINTS)]


class ApiModel(BaseModel):
    """Base model that rejects undocumented fields."""

    model_config = ConfigDict(extra="forbid")


class RiskLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RiskGroupCode(StrEnum):
    DEEPFAKE = "DEEPFAKE"
    IDENTITY = "IDENTITY"
    VEHICLE = "VEHICLE"
    CONTACT_ACCOUNT = "CONTACT_ACCOUNT"
    RESIDENCE = "RESIDENCE"
    BUILDING = "BUILDING"
    LOCATION = "LOCATION"


class DetectionType(StrEnum):
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
    EXIF_GPS = "EXIF_GPS"
    VISUAL_LOCATION_CLUE = "VISUAL_LOCATION_CLUE"
    TRAVEL_ITINERARY = "TRAVEL_ITINERARY"


class ProcessingAction(StrEnum):
    MASK = "MASK"
    APPLY_DEEPFAKE_PROTECTION = "APPLY_DEEPFAKE_PROTECTION"
    REMOVE_METADATA = "REMOVE_METADATA"


class ImageFormat(StrEnum):
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"


class DeepfakeSkipReason(StrEnum):
    NO_FACE_DETECTED = "NO_FACE_DETECTED"


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    INVALID_SOURCE_URL = "INVALID_SOURCE_URL"
    INVALID_RESULT_URL = "INVALID_RESULT_URL"
    INVALID_SELECTED_REGIONS = "INVALID_SELECTED_REGIONS"
    CORRUPTED_IMAGE = "CORRUPTED_IMAGE"
    SOURCE_URL_EXPIRED = "SOURCE_URL_EXPIRED"
    RESULT_URL_EXPIRED = "RESULT_URL_EXPIRED"
    IMAGE_ANALYSIS_MISMATCH = "IMAGE_ANALYSIS_MISMATCH"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    UNSUPPORTED_IMAGE_FORMAT = "UNSUPPORTED_IMAGE_FORMAT"
    RESULT_CONTENT_TYPE_MISMATCH = "RESULT_CONTENT_TYPE_MISMATCH"
    INVALID_REGION = "INVALID_REGION"
    REGION_OUT_OF_BOUNDS = "REGION_OUT_OF_BOUNDS"
    IMAGE_ANALYSIS_FAILED = "IMAGE_ANALYSIS_FAILED"
    IMAGE_PROCESSING_FAILED = "IMAGE_PROCESSING_FAILED"
    SOURCE_DOWNLOAD_FAILED = "SOURCE_DOWNLOAD_FAILED"
    RESULT_UPLOAD_FAILED = "RESULT_UPLOAD_FAILED"
    ANALYSIS_MODEL_UNAVAILABLE = "ANALYSIS_MODEL_UNAVAILABLE"
    DEEPFAKE_PROTECTION_FAILED = "DEEPFAKE_PROTECTION_FAILED"
    ANALYSIS_TIMEOUT = "ANALYSIS_TIMEOUT"
    PROCESSING_TIMEOUT = "PROCESSING_TIMEOUT"


DETECTION_GROUPS: dict[DetectionType, RiskGroupCode] = {
    DetectionType.FACE_EXPOSURE: RiskGroupCode.DEEPFAKE,
    DetectionType.NATIONAL_ID_CARD: RiskGroupCode.IDENTITY,
    DetectionType.DRIVERS_LICENSE: RiskGroupCode.IDENTITY,
    DetectionType.PASSPORT: RiskGroupCode.IDENTITY,
    DetectionType.STUDENT_ID_CARD: RiskGroupCode.IDENTITY,
    DetectionType.EMPLOYEE_ID_CARD: RiskGroupCode.IDENTITY,
    DetectionType.ACCESS_BADGE: RiskGroupCode.IDENTITY,
    DetectionType.PERSON_NAME: RiskGroupCode.IDENTITY,
    DetectionType.DATE_OF_BIRTH: RiskGroupCode.IDENTITY,
    DetectionType.RESIDENT_REGISTRATION_NUMBER: RiskGroupCode.IDENTITY,
    DetectionType.NAME_TAG: RiskGroupCode.IDENTITY,
    DetectionType.UNIFORM_REAL_NAME: RiskGroupCode.IDENTITY,
    DetectionType.SHIPPING_LABEL: RiskGroupCode.IDENTITY,
    DetectionType.VEHICLE_LICENSE_PLATE: RiskGroupCode.VEHICLE,
    DetectionType.MOTORCYCLE_LICENSE_PLATE: RiskGroupCode.VEHICLE,
    DetectionType.PARKING_STICKER: RiskGroupCode.VEHICLE,
    DetectionType.VEHICLE_REGISTRATION: RiskGroupCode.VEHICLE,
    DetectionType.PARKING_PASS: RiskGroupCode.VEHICLE,
    DetectionType.VEHICLE_CONTACT_NUMBER: RiskGroupCode.VEHICLE,
    DetectionType.PHONE_NUMBER: RiskGroupCode.CONTACT_ACCOUNT,
    DetectionType.EMAIL_ADDRESS: RiskGroupCode.CONTACT_ACCOUNT,
    DetectionType.SNS_HANDLE: RiskGroupCode.CONTACT_ACCOUNT,
    DetectionType.BUSINESS_CARD: RiskGroupCode.CONTACT_ACCOUNT,
    DetectionType.SCREEN_USERNAME: RiskGroupCode.CONTACT_ACCOUNT,
    DetectionType.PROFILE_INFORMATION: RiskGroupCode.CONTACT_ACCOUNT,
    DetectionType.ROAD_NAME_ADDRESS: RiskGroupCode.RESIDENCE,
    DetectionType.LOT_NUMBER_ADDRESS: RiskGroupCode.RESIDENCE,
    DetectionType.APARTMENT_UNIT: RiskGroupCode.RESIDENCE,
    DetectionType.APARTMENT_BRAND: RiskGroupCode.BUILDING,
    DetectionType.BUILDING_NUMBER: RiskGroupCode.BUILDING,
    DetectionType.BUILDING_NAME: RiskGroupCode.BUILDING,
    DetectionType.STORE_NAME: RiskGroupCode.BUILDING,
    DetectionType.SCHOOL_NAME: RiskGroupCode.BUILDING,
    DetectionType.COMPANY_NAME: RiskGroupCode.BUILDING,
    DetectionType.EXIF_GPS: RiskGroupCode.LOCATION,
    DetectionType.VISUAL_LOCATION_CLUE: RiskGroupCode.LOCATION,
    DetectionType.TRAVEL_ITINERARY: RiskGroupCode.LOCATION,
}


def _orientation(a: Point, b: Point, c: Point) -> int:
    cross_product = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if cross_product == 0:
        return 0
    return 1 if cross_product > 0 else -1


def _point_on_segment(a: Point, b: Point, point: Point) -> bool:
    return min(a[0], b[0]) <= point[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= point[1] <= max(
        a[1], b[1]
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    orientations = (
        _orientation(a, b, c),
        _orientation(a, b, d),
        _orientation(c, d, a),
        _orientation(c, d, b),
    )
    if orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0:
        return True
    return (
        (orientations[0] == 0 and _point_on_segment(a, b, c))
        or (orientations[1] == 0 and _point_on_segment(a, b, d))
        or (orientations[2] == 0 and _point_on_segment(c, d, a))
        or (orientations[3] == 0 and _point_on_segment(c, d, b))
    )


def _validate_polygon(polygon: list[Point]) -> list[Point]:
    if len(set(polygon)) != len(polygon):
        raise ValueError("polygon points must be unique")

    doubled_area = abs(
        sum(
            point[0] * polygon[(index + 1) % len(polygon)][1]
            - polygon[(index + 1) % len(polygon)][0] * point[1]
            for index, point in enumerate(polygon)
        )
    )
    if doubled_area == 0:
        raise ValueError("polygon must enclose a non-zero area")

    edge_count = len(polygon)
    for first_index in range(edge_count):
        first_start = polygon[first_index]
        first_end = polygon[(first_index + 1) % edge_count]
        for second_index in range(first_index + 1, edge_count):
            if second_index in {
                first_index,
                (first_index + 1) % edge_count,
                (first_index - 1) % edge_count,
            }:
                continue
            second_start = polygon[second_index]
            second_end = polygon[(second_index + 1) % edge_count]
            if _segments_intersect(first_start, first_end, second_start, second_end):
                raise ValueError("polygon edges must not intersect")
    return polygon


class BoundingBox(ApiModel):
    x: PixelCoordinate
    y: PixelCoordinate
    width: PositiveDimension
    height: PositiveDimension

    @model_validator(mode="after")
    def validate_maximum_bounds(self) -> Self:
        if self.x + self.width > MAX_IMAGE_DIMENSION:
            raise ValueError(f"bbox exceeds the maximum width of {MAX_IMAGE_DIMENSION}")
        if self.y + self.height > MAX_IMAGE_DIMENSION:
            raise ValueError(f"bbox exceeds the maximum height of {MAX_IMAGE_DIMENSION}")
        return self


class ImageRegion(ApiModel):
    bbox: BoundingBox
    polygon: Polygon

    _polygon_is_valid = field_validator("polygon")(_validate_polygon)


class AnalyzedImage(ApiModel):
    sha256: Sha256Hex
    width: PositiveDimension
    height: PositiveDimension
    format: ImageFormat
    orientation_normalized: Literal[True]


class RiskGroupSummary(ApiModel):
    code: RiskGroupCode
    label: NonEmptyString
    detected: StrictBool
    risk_level: RiskLevel
    detection_count: NonNegativeCount
    detection_ids: list[DetectionId]

    @model_validator(mode="after")
    def validate_detection_summary(self) -> Self:
        if len(set(self.detection_ids)) != len(self.detection_ids):
            raise ValueError("detection_ids must be unique")
        if self.detection_count != len(self.detection_ids):
            raise ValueError("detection_count must match detection_ids")
        if self.detected != (self.detection_count > 0):
            raise ValueError("detected must match detection_count")
        if not self.detected and self.risk_level is not RiskLevel.LOW:
            raise ValueError("a group without detections must have LOW risk")
        return self


class Detection(ApiModel):
    id: DetectionId
    risk_group: RiskGroupCode
    type: DetectionType
    label: NonEmptyString
    confidence: Confidence
    detected_text: NonEmptyString | None
    region: ImageRegion | None
    mask_supported: StrictBool
    processing_action: ProcessingAction

    @model_validator(mode="after")
    def validate_detection_contract(self) -> Self:
        expected_group = DETECTION_GROUPS[self.type]
        if self.risk_group is not expected_group:
            raise ValueError(f"{self.type} belongs to risk group {expected_group}")

        if self.type is DetectionType.FACE_EXPOSURE:
            if (
                self.processing_action is not ProcessingAction.APPLY_DEEPFAKE_PROTECTION
                or self.mask_supported
                or self.region is None
            ):
                raise ValueError("a face requires a region and mandatory deepfake protection")
        elif self.type is DetectionType.EXIF_GPS:
            if (
                self.processing_action is not ProcessingAction.REMOVE_METADATA
                or self.mask_supported
                or self.region is not None
            ):
                raise ValueError("EXIF GPS requires metadata removal without a region")
        elif (
            self.processing_action is not ProcessingAction.MASK
            or not self.mask_supported
            or self.region is None
        ):
            raise ValueError("visual detections require a maskable region")
        return self


class ImageAnalyzeRequest(ApiModel):
    source_object_key: ObjectKey
    source_download_url: HttpsUrl


class ImageAnalyzeResponse(ApiModel):
    request_id: RequestId
    source_object_key: ObjectKey
    image: AnalyzedImage
    risk_groups: Annotated[list[RiskGroupSummary], Field(min_length=7, max_length=7)]
    detections: list[Detection]

    @model_validator(mode="after")
    def validate_detection_references(self) -> Self:
        summaries = {summary.code: summary for summary in self.risk_groups}
        if len(summaries) != len(self.risk_groups) or set(summaries) != set(RiskGroupCode):
            raise ValueError("risk_groups must contain every risk group exactly once")

        detections_by_id = {detection.id: detection for detection in self.detections}
        if len(detections_by_id) != len(self.detections):
            raise ValueError("detection ids must be unique")

        for detection in self.detections:
            if detection.region is None:
                continue
            bbox = detection.region.bbox
            if bbox.x + bbox.width > self.image.width or bbox.y + bbox.height > self.image.height:
                raise ValueError(f"detection {detection.id} bbox is outside the image")
            if any(
                x > self.image.width or y > self.image.height for x, y in detection.region.polygon
            ):
                raise ValueError(f"detection {detection.id} polygon is outside the image")

        referenced_ids: set[str] = set()
        for code, summary in summaries.items():
            for detection_id in summary.detection_ids:
                detection = detections_by_id.get(detection_id)
                if detection is None:
                    raise ValueError(f"unknown detection id: {detection_id}")
                if detection.risk_group is not code:
                    raise ValueError(f"detection {detection_id} is referenced by the wrong group")
                referenced_ids.add(detection_id)

        if referenced_ids != set(detections_by_id):
            raise ValueError("every detection must be referenced by its risk group")
        return self


class SelectedRegion(ApiModel):
    detection_id: DetectionId
    risk_group: RiskGroupCode
    polygon: Polygon

    _polygon_is_valid = field_validator("polygon")(_validate_polygon)

    @model_validator(mode="after")
    def reject_deepfake_mask(self) -> Self:
        if self.risk_group is RiskGroupCode.DEEPFAKE:
            raise ValueError("deepfake detections are protected automatically and cannot be masked")
        return self


class ImageProcessRequest(ApiModel):
    source_object_key: ObjectKey
    source_download_url: HttpsUrl
    result_object_key: ObjectKey
    result_upload_url: HttpsUrl
    result_content_type: Literal["image/png"]
    analysis_image_sha256: Sha256Hex
    selected_regions: Annotated[list[SelectedRegion], Field(max_length=MAX_SELECTED_REGIONS)]
    remove_metadata: StrictBool

    @field_validator("selected_regions")
    @classmethod
    def validate_unique_detection_ids(cls, regions: list[SelectedRegion]) -> list[SelectedRegion]:
        detection_ids = [region.detection_id for region in regions]
        if len(set(detection_ids)) != len(detection_ids):
            raise ValueError("selected region detection_ids must be unique")
        return regions


class DeepfakeProtectionResult(ApiModel):
    attempted: Literal[True]
    applied: StrictBool
    skip_reason: DeepfakeSkipReason | None

    @model_validator(mode="after")
    def validate_skip_reason(self) -> Self:
        if self.applied and self.skip_reason is not None:
            raise ValueError("an applied protection must not have a skip_reason")
        if not self.applied and self.skip_reason is not DeepfakeSkipReason.NO_FACE_DETECTED:
            raise ValueError("a skipped protection must report NO_FACE_DETECTED")
        return self


class ImageProcessResponse(ApiModel):
    request_id: RequestId
    status: Literal["COMPLETED"]
    source_object_key: ObjectKey
    result_object_key: ObjectKey
    result_content_type: Literal["image/png"]
    masked_region_count: NonNegativeCount
    deepfake_protection: DeepfakeProtectionResult
    metadata_removed: StrictBool


class ApiError(ApiModel):
    code: ErrorCode
    message: NonEmptyString
    request_id: RequestId


class ErrorResponse(ApiModel):
    error: ApiError


# Concise endpoint-oriented aliases for route modules.
AnalyzeRequest = ImageAnalyzeRequest
AnalyzeResponse = ImageAnalyzeResponse
ProcessRequest = ImageProcessRequest
ProcessResponse = ImageProcessResponse
