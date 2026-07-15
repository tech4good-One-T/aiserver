"""Deterministic region validation and image masking operations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from app.core.errors import AppError

Point = tuple[int, int]


@dataclass(frozen=True, slots=True)
class MaskResult:
    image: Image.Image
    region_count: int


def _orientation(a: Point, b: Point, c: Point) -> int:
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    return (value > 0) - (value < 0)


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    return _orientation(a, b, c) != _orientation(a, b, d) and _orientation(c, d, a) != _orientation(
        c, d, b
    )


def validate_polygon(polygon: Sequence[Point], width: int, height: int) -> None:
    """Reject malformed, zero-area, self-intersecting, or out-of-bounds polygons."""
    if len(polygon) < 3 or len(set(polygon)) < 3:
        raise AppError(422, "INVALID_REGION", "블러 영역 다각형이 올바르지 않습니다.")
    if any(x < 0 or y < 0 or x >= width or y >= height for x, y in polygon):
        raise AppError(422, "REGION_OUT_OF_BOUNDS", "블러 영역이 이미지 범위를 벗어났습니다.")

    signed_area = sum(
        polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
        - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
        for index in range(len(polygon))
    )
    if signed_area == 0:
        raise AppError(422, "INVALID_REGION", "블러 영역 다각형의 면적이 없습니다.")

    edge_count = len(polygon)
    for first in range(edge_count):
        a, b = polygon[first], polygon[(first + 1) % edge_count]
        for second in range(first + 1, edge_count):
            if second in {first, (first + 1) % edge_count} or (second + 1) % edge_count == first:
                continue
            c, d = polygon[second], polygon[(second + 1) % edge_count]
            if _segments_cross(a, b, c, d):
                raise AppError(422, "INVALID_REGION", "블러 영역 다각형이 자기 교차합니다.")


def blur_regions(image: Image.Image, polygons: Sequence[Sequence[Point]]) -> MaskResult:
    """Blur only the union of validated polygons."""
    if not polygons:
        return MaskResult(image=image.copy(), region_count=0)

    rgb = np.asarray(image.convert("RGB"))
    mask = np.zeros((image.height, image.width), dtype=np.uint8)
    for polygon in polygons:
        validate_polygon(polygon, image.width, image.height)
        cv2.fillPoly(mask, [np.asarray(polygon, dtype=np.int32)], 255)

    kernel = max(15, min(image.width, image.height) // 25)
    kernel = kernel if kernel % 2 == 1 else kernel + 1
    blurred = cv2.GaussianBlur(rgb, (kernel, kernel), 0)
    combined = np.where(mask[:, :, None] > 0, blurred, rgb)
    region_count = max(0, cv2.connectedComponents(mask)[0] - 1)
    return MaskResult(
        image=Image.fromarray(combined.astype(np.uint8), mode="RGB"), region_count=region_count
    )
