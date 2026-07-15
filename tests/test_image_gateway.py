import asyncio
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.services.image_gateway import ImageGateway


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "allowed_storage_hosts": frozenset({"bucket.example.com"}),
        "storage_bucket": None,
        "max_image_bytes": 1024,
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


def test_download_returns_bytes_and_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, content=b"image", headers={"Content-Type": "image/png"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ImageGateway(_settings(), client)

    result = asyncio.run(
        gateway.download(
            "https://bucket.example.com/original/image.png?signature=secret",
            "original/image.png",
        )
    )
    asyncio.run(client.aclose())

    assert result.data == b"image"
    assert result.content_type == "image/png"


def test_download_rejects_non_allowlisted_host() -> None:
    gateway = ImageGateway(_settings())

    with pytest.raises(AppError) as error:
        asyncio.run(
            gateway.download(
                "https://attacker.example/original/image.png?signature=secret",
                "original/image.png",
            )
        )

    assert error.value.code == "INVALID_SOURCE_URL"


def test_download_rejects_non_https_port_on_allowlisted_host() -> None:
    gateway = ImageGateway(_settings())

    with pytest.raises(AppError) as error:
        asyncio.run(
            gateway.download(
                "https://bucket.example.com:8443/original/image.png?signature=secret",
                "original/image.png",
            )
        )

    assert error.value.code == "INVALID_SOURCE_URL"


def test_download_accepts_configured_path_style_bucket() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"image", headers={"Content-Type": "image/png"})
        )
    )
    gateway = ImageGateway(
        _settings(
            allowed_storage_hosts=frozenset({"s3.ap-northeast-2.amazonaws.com"}),
            storage_bucket="expected-bucket",
        ),
        client,
    )

    result = asyncio.run(
        gateway.download(
            "https://s3.ap-northeast-2.amazonaws.com/expected-bucket/original/image.png",
            "original/image.png",
        )
    )
    asyncio.run(client.aclose())

    assert result.data == b"image"


def test_download_rejects_other_path_style_bucket() -> None:
    gateway = ImageGateway(
        _settings(
            allowed_storage_hosts=frozenset({"s3.ap-northeast-2.amazonaws.com"}),
            storage_bucket="expected-bucket",
        )
    )

    with pytest.raises(AppError) as error:
        asyncio.run(
            gateway.download(
                "https://s3.ap-northeast-2.amazonaws.com/other-bucket/original/image.png",
                "other-bucket/original/image.png",
            )
        )

    assert error.value.code == "INVALID_SOURCE_URL"


def test_download_rejects_redirect() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"Location": "https://x"})
        )
    )
    gateway = ImageGateway(_settings(), client)

    with pytest.raises(AppError) as error:
        asyncio.run(
            gateway.download(
                "https://bucket.example.com/original/image.png?signature=secret",
                "original/image.png",
            )
        )
    asyncio.run(client.aclose())

    assert error.value.code == "SOURCE_DOWNLOAD_FAILED"


def test_upload_png_sends_signed_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.headers["content-type"] == "image/png"
        assert request.content == b"png"
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ImageGateway(_settings(), client)

    asyncio.run(
        gateway.upload_png(
            "https://bucket.example.com/protected/image.png?signature=secret",
            "protected/image.png",
            b"png",
        )
    )
    asyncio.run(client.aclose())
