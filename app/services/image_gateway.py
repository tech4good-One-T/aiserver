"""Restricted HTTP transfer client for presigned object-storage URLs."""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpx

from app.core.config import Settings
from app.core.errors import AppError


@dataclass(frozen=True, slots=True)
class DownloadedObject:
    data: bytes
    content_type: str


class ImageGateway:
    """Download and upload one image without exposing storage credentials."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client

    def _validate_url(self, url: str, object_key: str, *, result: bool) -> None:
        error = AppError(
            400,
            "INVALID_RESULT_URL" if result else "INVALID_SOURCE_URL",
            "결과 업로드 URL이 올바르지 않습니다."
            if result
            else "원본 다운로드 URL이 올바르지 않습니다.",
        )
        try:
            parsed = urlsplit(url)
            hostname = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError as exc:
            raise error from exc

        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in {None, 443}
            or hostname not in self._settings.allowed_storage_hosts
        ):
            raise error

        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private or address.is_loopback or address.is_link_local
        ):
            raise error

        decoded_path = unquote(parsed.path).lstrip("/")
        bucket = self._settings.storage_bucket
        if bucket:
            if not decoded_path.startswith(f"{bucket}/"):
                raise error
            decoded_path = decoded_path[len(bucket) + 1 :]
        if (
            not object_key
            or decoded_path != object_key
            or ".." in object_key.split("/")
            or "\\" in object_key
        ):
            raise error

    @asynccontextmanager
    async def _http_client(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        timeout = httpx.Timeout(self._settings.storage_timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            yield client

    async def download(self, url: str, object_key: str) -> DownloadedObject:
        """Download a size-limited object from an allowlisted URL."""
        self._validate_url(url, object_key, result=False)
        try:
            async with self._http_client() as client, client.stream("GET", url) as response:
                if response.status_code in {401, 403}:
                    raise AppError(403, "SOURCE_URL_EXPIRED", "원본 다운로드 URL이 만료되었습니다.")
                if response.is_redirect or response.status_code >= 400:
                    raise AppError(
                        502, "SOURCE_DOWNLOAD_FAILED", "원본 이미지를 다운로드하지 못했습니다."
                    )

                declared_length = response.headers.get("content-length")
                if declared_length and int(declared_length) > self._settings.max_image_bytes:
                    raise AppError(413, "IMAGE_TOO_LARGE", "이미지 크기 제한을 초과했습니다.")

                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self._settings.max_image_bytes:
                        raise AppError(413, "IMAGE_TOO_LARGE", "이미지 크기 제한을 초과했습니다.")
                return DownloadedObject(
                    data=bytes(content),
                    content_type=response.headers.get("content-type", ""),
                )
        except AppError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                502, "SOURCE_DOWNLOAD_FAILED", "원본 이미지를 다운로드하지 못했습니다."
            ) from exc

    async def upload_png(self, url: str, object_key: str, data: bytes) -> None:
        """Upload a generated PNG to an allowlisted presigned URL."""
        self._validate_url(url, object_key, result=True)
        try:
            async with self._http_client() as client:
                response = await client.put(
                    url, content=data, headers={"Content-Type": "image/png"}
                )
            if response.status_code in {401, 403}:
                raise AppError(403, "RESULT_URL_EXPIRED", "결과 업로드 URL이 만료되었습니다.")
            if response.is_redirect or response.status_code >= 400:
                raise AppError(502, "RESULT_UPLOAD_FAILED", "결과 이미지를 업로드하지 못했습니다.")
        except AppError:
            raise
        except httpx.HTTPError as exc:
            raise AppError(
                502, "RESULT_UPLOAD_FAILED", "결과 이미지를 업로드하지 못했습니다."
            ) from exc
