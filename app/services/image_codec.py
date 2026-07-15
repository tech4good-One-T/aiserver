"""Safe image decoding, orientation normalization, and encoding."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError

from app.core.config import Settings
from app.core.errors import AppError

SUPPORTED_FORMATS = {
    "JPEG": ("jpeg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}
GEMINI_MAX_INLINE_BYTES = 14 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DecodedImage:
    """A canonical RGB image with metadata needed by both endpoints."""

    image: Image.Image
    source_format: str
    sha256: str
    normalized_png: bytes
    has_gps: bool
    preserved_exif: bytes | None
    metadata_fingerprint: bytes
    gemini_bytes: bytes
    gemini_mime_type: str

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


def _has_gps(exif: Image.Exif) -> bool:
    gps_tag = getattr(ExifTags.IFD, "GPSInfo", 34853)
    try:
        return bool(exif.get_ifd(gps_tag))
    except (KeyError, TypeError, ValueError):
        try:
            return bool(exif.get(gps_tag))
        except (KeyError, TypeError, ValueError):
            return False


def _normalized_exif(exif: Image.Exif) -> bytes | None:
    if not exif:
        return None
    normalized = Image.Exif()
    normalized.load(exif.tobytes())
    orientation_tag = getattr(ExifTags.Base, "Orientation", 274)
    normalized[orientation_tag] = 1
    return normalized.tobytes()


def _metadata_fingerprint(source: Image.Image, preserved_exif: bytes | None) -> bytes:
    """Serialize metadata deterministically for the analysis/processing fingerprint."""
    chunks = [b"image-metadata-v1", preserved_exif or b""]
    for key in sorted(source.info, key=str):
        if key == "exif":
            continue
        value = source.info[key]
        if isinstance(value, bytes):
            encoded = value
        elif isinstance(value, str):
            encoded = value.encode("utf-8")
        else:
            encoded = repr(value).encode("utf-8")
        key_bytes = str(key).encode("utf-8")
        chunks.extend((key_bytes, len(encoded).to_bytes(8, "big"), encoded))
    return b"\x00".join(chunks)


def encode_png(image: Image.Image, *, exif: bytes | None = None) -> bytes:
    """Encode an image exactly once as PNG, optionally preserving normalized EXIF."""
    output = BytesIO()
    save_options: dict[str, object] = {"format": "PNG", "compress_level": 6}
    if exif:
        save_options["exif"] = exif
    image.convert("RGB").save(output, **save_options)
    return output.getvalue()


def _encode_gemini_transport(
    image: Image.Image,
    *,
    normalized_png: bytes | None = None,
) -> tuple[bytes, str]:
    """Encode a bounded, metadata-free image for Gemini inline transport.

    Keep the canonical PNG when it already fits the provider's inline limit so
    small text stays lossless. Larger images fall back to bounded JPEG transport;
    this transport is never used for hashing or for the output image.
    """
    if normalized_png is not None and len(normalized_png) <= GEMINI_MAX_INLINE_BYTES:
        return normalized_png, "image/png"

    quality_levels = (90, 85, 80, 75, 70, 60, 50, 40)
    working = image.convert("RGB")
    while True:
        for quality in quality_levels:
            output = BytesIO()
            working.save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=False,
            )
            encoded = output.getvalue()
            if len(encoded) <= GEMINI_MAX_INLINE_BYTES:
                return encoded, "image/jpeg"

        largest_dimension = max(working.width, working.height)
        if largest_dimension <= 512:
            raise AppError(
                413,
                "IMAGE_TOO_LARGE",
                "Gemini 분석 전송 크기 제한을 초과했습니다.",
            )
        scale = 0.8
        resized = working.resize(
            (
                max(1, round(working.width * scale)),
                max(1, round(working.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
        working = resized


def decode_image(data: bytes, content_type: str, settings: Settings) -> DecodedImage:
    """Validate and canonicalize an untrusted image payload."""
    if len(data) > settings.max_image_bytes:
        raise AppError(413, "IMAGE_TOO_LARGE", "이미지 크기 제한을 초과했습니다.")

    normalized_content_type = content_type.partition(";")[0].strip().lower()
    supported_content_types = {item[1] for item in SUPPORTED_FORMATS.values()}
    if normalized_content_type not in supported_content_types:
        raise AppError(415, "UNSUPPORTED_IMAGE_FORMAT", "지원하지 않는 이미지 형식입니다.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                source.load()
                format_info = SUPPORTED_FORMATS.get(source.format or "")
                if format_info is None or format_info[1] != normalized_content_type:
                    raise AppError(
                        415,
                        "UNSUPPORTED_IMAGE_FORMAT",
                        "이미지 형식과 Content-Type이 일치하지 않습니다.",
                    )
                if getattr(source, "n_frames", 1) > 1 or getattr(source, "is_animated", False):
                    raise AppError(
                        415,
                        "UNSUPPORTED_IMAGE_FORMAT",
                        "애니메이션 이미지는 지원하지 않습니다.",
                    )

                if (
                    source.width > settings.max_image_dimension
                    or source.height > settings.max_image_dimension
                ):
                    raise AppError(
                        413,
                        "IMAGE_TOO_LARGE",
                        "이미지 해상도 제한을 초과했습니다.",
                    )

                exif = source.getexif()
                has_gps = _has_gps(exif)
                preserved_exif = _normalized_exif(exif)
                metadata_fingerprint = _metadata_fingerprint(source, preserved_exif)
                normalized = ImageOps.exif_transpose(source).convert("RGB")
                normalized_png = encode_png(normalized)
                gemini_bytes, gemini_mime_type = _encode_gemini_transport(
                    normalized,
                    normalized_png=normalized_png,
                )
    except AppError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AppError(413, "IMAGE_TOO_LARGE", "이미지 해상도 제한을 초과했습니다.") from exc
    except UnidentifiedImageError as exc:
        raise AppError(400, "CORRUPTED_IMAGE", "이미지를 해석할 수 없습니다.") from exc
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise AppError(400, "CORRUPTED_IMAGE", "이미지를 해석할 수 없습니다.") from exc

    return DecodedImage(
        image=normalized,
        source_format=format_info[0],
        sha256=sha256(normalized_png + b"\x00" + metadata_fingerprint).hexdigest(),
        normalized_png=normalized_png,
        has_gps=has_gps,
        preserved_exif=preserved_exif,
        metadata_fingerprint=metadata_fingerprint,
        gemini_bytes=gemini_bytes,
        gemini_mime_type=gemini_mime_type,
    )
