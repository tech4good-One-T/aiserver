from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.api.dependencies import (
    get_faceshield_adapter,
    get_gemini_analyzer,
    get_image_gateway,
)
from app.core.config import Settings, get_settings
from app.main import app
from app.services.faceshield import FaceShieldExecutionError
from app.services.gemini_analyzer import GeminiAnalysisResult, GeminiRawDetection
from app.services.image_codec import decode_image
from app.services.image_gateway import DownloadedObject


def _settings() -> Settings:
    return Settings(
        app_env="test",
        allowed_storage_hosts=frozenset({"bucket.example.com"}),
        storage_bucket=None,
        max_image_bytes=10 * 1024 * 1024,
        max_image_dimension=4096,
        storage_timeout_seconds=30,
        analysis_timeout_seconds=60,
        processing_timeout_seconds=600,
        gemini_api_key=None,
        gemini_model="gemini-2.5-flash",
        faceshield_repo_path=Path("/tmp/faceshield"),
        faceshield_command="bash execute.sh",
    )


def _source_png() -> bytes:
    image = Image.new("RGB", (40, 40), "white")
    for x in range(8, 32):
        for y in range(8, 32):
            image.putpixel((x, y), (0, 0, 0) if (x + y) % 2 else (255, 255, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class FakeGateway:
    def __init__(self, source: bytes) -> None:
        self.source = source
        self.uploaded: bytes | None = None

    async def download(self, url: str, object_key: str) -> DownloadedObject:
        return DownloadedObject(data=self.source, content_type="image/png")

    async def upload_png(self, url: str, object_key: str, data: bytes) -> None:
        self.uploaded = data


class FakeAnalyzer:
    def __init__(self, result: GeminiAnalysisResult) -> None:
        self.result = result
        self.call_count = 0

    async def analyze(
        self,
        normalized_png: bytes,
        mime_type: str = "image/png",
    ) -> GeminiAnalysisResult:
        self.call_count += 1
        return self.result


class FakeFaceShield:
    def __init__(
        self,
        *,
        fail: bool = False,
        output_size: tuple[int, int] | None = None,
        identity: bool = False,
    ) -> None:
        self.fail = fail
        self.output_size = output_size
        self.identity = identity
        self.call_count = 0

    async def protect_png(self, normalized_png: bytes) -> bytes:
        self.call_count += 1
        if self.fail:
            raise FaceShieldExecutionError(1)
        if self.identity:
            return normalized_png
        if self.output_size is not None:
            with Image.open(BytesIO(normalized_png)) as image:
                resized = image.convert("RGB").resize(self.output_size)
            red, green, blue = resized.getpixel((0, 0))
            resized.putpixel((0, 0), (max(0, red - 1), green, blue))
            output = BytesIO()
            resized.save(output, format="PNG")
            return output.getvalue()
        with Image.open(BytesIO(normalized_png)) as image:
            protected = image.convert("RGB")
        red, green, blue = protected.getpixel((0, 0))
        protected.putpixel((0, 0), (max(0, red - 1), green, blue))
        output = BytesIO()
        protected.save(output, format="PNG")
        return output.getvalue()


def _face_analysis() -> GeminiAnalysisResult:
    return GeminiAnalysisResult(
        detections=[
            GeminiRawDetection(
                risk_group="DEEPFAKE",
                type="FACE_EXPOSURE",
                confidence=0.97,
                detected_text=None,
                box_2d=[200, 200, 800, 800],
            )
        ]
    )


def _analyze_payload() -> dict[str, object]:
    return {
        "source_object_key": "original/image.png",
        "source_download_url": (
            "https://bucket.example.com/original/image.png?X-Amz-Signature=secret"
        ),
    }


def _process_payload(source: bytes, settings: Settings) -> dict[str, object]:
    image_hash = decode_image(source, "image/png", settings).sha256
    return {
        "source_object_key": "original/image.png",
        "source_download_url": (
            "https://bucket.example.com/original/image.png?X-Amz-Signature=secret"
        ),
        "result_object_key": "protected/image.png",
        "result_upload_url": (
            "https://bucket.example.com/protected/image.png?X-Amz-Signature=secret"
        ),
        "result_content_type": "image/png",
        "analysis_image_sha256": image_hash,
        "selected_regions": [
            {
                "detection_id": "det_vehicle_license_plate_001",
                "risk_group": "VEHICLE",
                "polygon": [[8, 8], [31, 8], [31, 31], [8, 31]],
            }
        ],
        "remove_metadata": True,
    }


def _client(
    settings: Settings,
    gateway: FakeGateway,
    analyzer: FakeAnalyzer,
    faceshield: FakeFaceShield,
) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_image_gateway] = lambda: gateway
    app.dependency_overrides[get_gemini_analyzer] = lambda: analyzer
    app.dependency_overrides[get_faceshield_adapter] = lambda: faceshield
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_analyze_returns_seven_groups_and_pixel_detection() -> None:
    settings = _settings()
    gateway = FakeGateway(_source_png())
    analyzer = FakeAnalyzer(_face_analysis())
    faceshield = FakeFaceShield()

    try:
        with _client(settings, gateway, analyzer, faceshield) as client:
            response = client.post("/api/v1/images/analyze", json=_analyze_payload())
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert len(body["risk_groups"]) == 7
    assert body["image"]["width"] == 40
    assert body["image"]["orientation_normalized"] is True
    assert body["detections"][0]["type"] == "FACE_EXPOSURE"
    assert body["detections"][0]["region"]["polygon"] == [
        [8, 8],
        [31, 8],
        [31, 31],
        [8, 31],
    ]
    assert body["request_id"].startswith("req_")
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_process_blurs_uploads_and_skips_faceshield_without_face() -> None:
    settings = _settings()
    source = _source_png()
    gateway = FakeGateway(source)
    analyzer = FakeAnalyzer(GeminiAnalysisResult())
    faceshield = FakeFaceShield()

    try:
        with _client(settings, gateway, analyzer, faceshield) as client:
            response = client.post(
                "/api/v1/images/process",
                json=_process_payload(source, settings),
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["masked_region_count"] == 1
    assert response.json()["deepfake_protection"] == {
        "attempted": True,
        "applied": False,
        "skip_reason": "NO_FACE_DETECTED",
    }
    assert faceshield.call_count == 0
    assert gateway.uploaded is not None
    assert gateway.uploaded.startswith(b"\x89PNG\r\n\x1a\n")
    assert gateway.uploaded != source


def test_process_always_applies_faceshield_when_face_is_detected() -> None:
    settings = _settings()
    source = _source_png()
    gateway = FakeGateway(source)
    analyzer = FakeAnalyzer(_face_analysis())
    faceshield = FakeFaceShield()

    try:
        with _client(settings, gateway, analyzer, faceshield) as client:
            response = client.post(
                "/api/v1/images/process",
                json=_process_payload(source, settings),
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["deepfake_protection"] == {
        "attempted": True,
        "applied": True,
        "skip_reason": None,
    }
    assert faceshield.call_count == 1
    assert gateway.uploaded is not None


def test_process_preserves_photo_dimensions_when_faceshield_resizes_crop() -> None:
    settings = _settings()
    source = _source_png()
    gateway = FakeGateway(source)
    analyzer = FakeAnalyzer(_face_analysis())
    faceshield = FakeFaceShield(output_size=(10, 10))

    try:
        with _client(settings, gateway, analyzer, faceshield) as client:
            response = client.post(
                "/api/v1/images/process",
                json=_process_payload(source, settings),
            )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert gateway.uploaded is not None
    with Image.open(BytesIO(gateway.uploaded)) as result:
        assert result.size == (40, 40)


def test_process_rejects_image_hash_mismatch_before_model_or_upload() -> None:
    settings = _settings()
    source = _source_png()
    gateway = FakeGateway(source)
    analyzer = FakeAnalyzer(_face_analysis())
    faceshield = FakeFaceShield()
    payload = _process_payload(source, settings)
    payload["analysis_image_sha256"] = "f" * 64

    try:
        with _client(settings, gateway, analyzer, faceshield) as client:
            response = client.post("/api/v1/images/process", json=payload)
    finally:
        _clear_overrides()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IMAGE_ANALYSIS_MISMATCH"
    assert analyzer.call_count == 0
    assert faceshield.call_count == 0
    assert gateway.uploaded is None


def test_process_does_not_upload_unprotected_image_when_faceshield_fails() -> None:
    settings = _settings()
    source = _source_png()
    gateway = FakeGateway(source)
    analyzer = FakeAnalyzer(_face_analysis())
    faceshield = FakeFaceShield(fail=True)

    try:
        with _client(settings, gateway, analyzer, faceshield) as client:
            response = client.post(
                "/api/v1/images/process",
                json=_process_payload(source, settings),
            )
    finally:
        _clear_overrides()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEEPFAKE_PROTECTION_FAILED"
    assert gateway.uploaded is None


def test_process_rejects_identity_faceshield_output() -> None:
    settings = _settings()
    source = _source_png()
    gateway = FakeGateway(source)
    analyzer = FakeAnalyzer(_face_analysis())
    faceshield = FakeFaceShield(identity=True)

    try:
        with _client(settings, gateway, analyzer, faceshield) as client:
            response = client.post(
                "/api/v1/images/process",
                json=_process_payload(source, settings),
            )
    finally:
        _clear_overrides()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEEPFAKE_PROTECTION_FAILED"
    assert gateway.uploaded is None


def test_invalid_source_url_uses_common_error_envelope() -> None:
    settings = _settings()
    gateway = FakeGateway(_source_png())
    analyzer = FakeAnalyzer(GeminiAnalysisResult())
    faceshield = FakeFaceShield()
    payload = _analyze_payload()
    payload["source_download_url"] = "http://bucket.example.com/original/image.png"

    try:
        with _client(settings, gateway, analyzer, faceshield) as client:
            response = client.post("/api/v1/images/analyze", json=payload)
    finally:
        _clear_overrides()

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_SOURCE_URL"
    assert body["error"]["request_id"].startswith("req_")
