"""Tests for the Gemini visual-risk provider."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

import app.services.gemini_analyzer as gemini_module
from app.services.gemini_analyzer import (
    GeminiAnalyzer,
    GeminiAnalyzerInputError,
    GeminiAnalyzerInvalidResponseError,
    GeminiAnalyzerTimeoutError,
    GeminiAnalyzerUnavailableError,
    GeminiDetectionType,
    GeminiRiskGroup,
)

NORMALIZED_PNG = gemini_module.PNG_SIGNATURE + b"normalized-image-payload"


class _FakePart:
    @classmethod
    def from_bytes(cls, *, data: bytes, mime_type: str) -> dict[str, object]:
        return {"data": data, "mime_type": mime_type}


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: object) -> None:
        self.values = kwargs


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[..., Any],
) -> dict[str, Any]:
    state: dict[str, Any] = {}

    class FakeModels:
        async def generate_content(self, **kwargs: object) -> Any:
            state["request"] = kwargs
            result = handler(**kwargs)
            if asyncio.iscoroutine(result):
                return await result
            if isinstance(result, Exception):
                raise result
            return result

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            state["client_options"] = kwargs
            self.aio = SimpleNamespace(models=FakeModels())

    fake_genai = SimpleNamespace(Client=FakeClient)
    fake_types = SimpleNamespace(
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )
    monkeypatch.setattr(gemini_module, "_load_google_sdk", lambda: (fake_genai, fake_types))
    return state


def test_analyze_valid_structured_response_returns_allowlisted_detection(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {
        "detections": [
            {
                "risk_group": "VEHICLE",
                "type": "VEHICLE_LICENSE_PLATE",
                "confidence": 0.94,
                "detected_text": "12가3456",
                "box_2d": [700, 400, 800, 650],
            }
        ]
    }
    state = _install_fake_sdk(
        monkeypatch,
        lambda **_: SimpleNamespace(parsed=payload, text=None),
    )
    analyzer = GeminiAnalyzer(api_key="test-key", model="gemini-test", timeout_seconds=2)

    result = asyncio.run(analyzer.analyze(NORMALIZED_PNG))

    assert len(result.detections) == 1
    assert result.detections[0].risk_group is GeminiRiskGroup.VEHICLE
    assert result.detections[0].type is GeminiDetectionType.VEHICLE_LICENSE_PLATE
    assert state["request"]["model"] == "gemini-test"
    assert state["request"]["contents"][0] == {
        "data": NORMALIZED_PNG,
        "mime_type": "image/png",
    }
    config = state["request"]["config"]
    assert config.values["response_mime_type"] == "application/json"
    assert "response_schema" not in config.values
    assert "top-level JSON value must be an object" in state["request"]["contents"][1]
    assert '{"detections": []}' in state["request"]["contents"][1]
    assert state["client_options"]["http_options"] == {"timeout": 2000}
    assert "12가3456" not in caplog.text
    assert "normalized-image-payload" not in caplog.text


def test_analyze_json_text_fallback_returns_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(
        monkeypatch,
        lambda **_: SimpleNamespace(parsed=None, text='{"detections": []}'),
    )
    analyzer = GeminiAnalyzer(api_key="test-key")

    result = asyncio.run(analyzer.analyze(NORMALIZED_PNG))

    assert result.detections == []


@pytest.mark.parametrize(
    "detection",
    [
        {
            "risk_group": "IDENTITY",
            "type": "VEHICLE_LICENSE_PLATE",
            "confidence": 0.9,
            "detected_text": None,
            "box_2d": [1, 2, 30, 40],
        },
        {
            "risk_group": "VEHICLE",
            "type": "UNKNOWN_TYPE",
            "confidence": 0.9,
            "detected_text": None,
            "box_2d": [1, 2, 30, 40],
        },
        {
            "risk_group": "VEHICLE",
            "type": "VEHICLE_LICENSE_PLATE",
            "confidence": 0.9,
            "detected_text": None,
            "box_2d": [10, 2, 10, 40],
        },
        {
            "risk_group": "VEHICLE",
            "type": "VEHICLE_LICENSE_PLATE",
            "confidence": 0.9,
            "detected_text": None,
            "box_2d": [1, 2, 30, 1001],
        },
    ],
)
def test_analyze_invalid_model_detection_raises_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
    detection: dict[str, object],
) -> None:
    _install_fake_sdk(
        monkeypatch,
        lambda **_: SimpleNamespace(parsed={"detections": [detection]}, text=None),
    )
    analyzer = GeminiAnalyzer(api_key="test-key")

    with pytest.raises(GeminiAnalyzerInvalidResponseError):
        asyncio.run(analyzer.analyze(NORMALIZED_PNG))


def test_analyze_non_png_rejects_before_sdk_call(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fail_if_loaded() -> tuple[None, None]:
        nonlocal called
        called = True
        return None, None

    monkeypatch.setattr(gemini_module, "_load_google_sdk", fail_if_loaded)
    analyzer = GeminiAnalyzer(api_key="test-key")

    with pytest.raises(GeminiAnalyzerInputError):
        asyncio.run(analyzer.analyze(b"not-a-png"))

    assert called is False


def test_analyze_accepts_jpeg_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_sdk(
        monkeypatch,
        lambda **_: SimpleNamespace(parsed={"detections": []}, text=None),
    )
    analyzer = GeminiAnalyzer(api_key="test-key")

    result = asyncio.run(analyzer.analyze(b"\xff\xd8\xffjpeg", "image/jpeg"))

    assert result.detections == []
    assert state["request"]["contents"][0]["mime_type"] == "image/jpeg"


def test_analyze_deadline_raises_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def delayed_response(**_: object) -> SimpleNamespace:
        await asyncio.sleep(0.05)
        return SimpleNamespace(parsed={"detections": []}, text=None)

    _install_fake_sdk(monkeypatch, delayed_response)
    analyzer = GeminiAnalyzer(api_key="test-key", timeout_seconds=0.001)

    with pytest.raises(GeminiAnalyzerTimeoutError):
        asyncio.run(analyzer.analyze(NORMALIZED_PNG))


def test_analyze_missing_key_raises_unavailable_without_loading_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        gemini_module,
        "_load_google_sdk",
        lambda: pytest.fail("SDK must not load without an API key"),
    )
    analyzer = GeminiAnalyzer()

    with pytest.raises(GeminiAnalyzerUnavailableError):
        asyncio.run(analyzer.analyze(NORMALIZED_PNG))


def test_analyze_sdk_failure_raises_sanitized_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(
        monkeypatch,
        lambda **_: RuntimeError("provider payload contained private-text"),
    )
    analyzer = GeminiAnalyzer(api_key="test-key")

    with pytest.raises(GeminiAnalyzerUnavailableError) as captured:
        asyncio.run(analyzer.analyze(NORMALIZED_PNG))

    assert "private-text" not in str(captured.value)


def test_aclose_releases_initialized_async_transport() -> None:
    state = {"closed": False}

    async def close() -> None:
        state["closed"] = True

    analyzer = GeminiAnalyzer(api_key="test-key")
    analyzer._client = SimpleNamespace(aio=SimpleNamespace(aclose=close))
    analyzer._types = SimpleNamespace()

    asyncio.run(analyzer.aclose())

    assert state["closed"] is True
    assert analyzer._client is None
    assert analyzer._types is None
