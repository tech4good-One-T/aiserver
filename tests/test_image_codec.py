from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import app.services.image_codec as image_codec
from app.core.config import Settings
from app.core.errors import AppError
from app.services.image_codec import decode_image


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "allowed_storage_hosts": frozenset({"bucket.example.com"}),
        "storage_bucket": None,
        "max_image_bytes": 10 * 1024 * 1024,
        "max_image_dimension": 4096,
        "storage_timeout_seconds": 30,
        "analysis_timeout_seconds": 60,
        "processing_timeout_seconds": 600,
        "prompt_edit_timeout_seconds": 600,
        "gemini_api_key": None,
        "gemini_model": "gemini-2.5-flash",
        "gemini_image_model": "gemini-3.1-flash-image",
        "faceshield_repo_path": Path("/tmp/faceshield"),
        "faceshield_command": "sh run.sh",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _image_bytes(format_name: str = "JPEG", *, width: int = 16, height: int = 12) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "red").save(output, format=format_name)
    return output.getvalue()


def test_decode_image_normalizes_exif_orientation() -> None:
    output = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (16, 12), "red").save(output, format="JPEG", exif=exif)

    decoded = decode_image(output.getvalue(), "image/jpeg", _settings())

    assert (decoded.width, decoded.height) == (12, 16)
    assert decoded.source_format == "jpeg"
    assert len(decoded.sha256) == 64


def test_decode_image_keeps_small_normalized_png_for_gemini() -> None:
    decoded = decode_image(_image_bytes("PNG"), "image/png", _settings())

    assert decoded.gemini_mime_type == "image/png"
    assert decoded.gemini_bytes == decoded.normalized_png


def test_decode_image_rejects_mismatched_content_type() -> None:
    with pytest.raises(AppError) as error:
        decode_image(_image_bytes("PNG"), "image/jpeg", _settings())

    assert error.value.code == "UNSUPPORTED_IMAGE_FORMAT"


def test_decode_image_rejects_animated_input(monkeypatch: pytest.MonkeyPatch) -> None:
    class AnimatedSource:
        format = "PNG"
        width = 16
        height = 12
        n_frames = 2
        is_animated = True

        def load(self) -> None:
            return None

        def __enter__(self) -> "AnimatedSource":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("app.services.image_codec.Image.open", lambda _: AnimatedSource())

    with pytest.raises(AppError) as error:
        decode_image(_image_bytes("PNG"), "image/png", _settings())

    assert error.value.code == "UNSUPPORTED_IMAGE_FORMAT"


def test_decode_image_rejects_oversized_dimensions() -> None:
    with pytest.raises(AppError) as error:
        decode_image(
            _image_bytes(width=20, height=10),
            "image/jpeg",
            _settings(max_image_dimension=16),
        )

    assert error.value.code == "IMAGE_TOO_LARGE"


def test_decode_image_hash_is_stable_for_same_pixels() -> None:
    image = _image_bytes()

    first = decode_image(image, "image/jpeg", _settings())
    second = decode_image(image, "image/jpeg", _settings())

    assert first.sha256 == second.sha256
    assert first.normalized_png == second.normalized_png


def test_decode_image_hash_changes_when_only_exif_changes() -> None:
    first_output = BytesIO()
    first_exif = Image.Exif()
    first_exif[37510] = b"first-comment"
    Image.new("RGB", (16, 12), "red").save(first_output, format="JPEG", exif=first_exif)

    second_output = BytesIO()
    second_exif = Image.Exif()
    second_exif[37510] = b"second-comment"
    Image.new("RGB", (16, 12), "red").save(second_output, format="JPEG", exif=second_exif)

    first = decode_image(first_output.getvalue(), "image/jpeg", _settings())
    second = decode_image(second_output.getvalue(), "image/jpeg", _settings())

    assert first.normalized_png == second.normalized_png
    assert first.sha256 != second.sha256


def test_decode_image_builds_bounded_metadata_free_gemini_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(image_codec, "GEMINI_MAX_INLINE_BYTES", 700)

    decoded = image_codec.decode_image(
        _image_bytes("PNG", width=256, height=256),
        "image/png",
        _settings(),
    )

    assert decoded.gemini_mime_type == "image/jpeg"
    assert len(decoded.gemini_bytes) <= 1_000
    assert decoded.gemini_bytes.startswith(b"\xff\xd8\xff")
