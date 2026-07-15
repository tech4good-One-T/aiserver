import pytest
from PIL import Image

from app.core.errors import AppError
from app.services.image_processing import blur_regions, validate_polygon


def test_blur_regions_changes_only_selected_pixels() -> None:
    image = Image.new("RGB", (40, 40), "white")
    for x in range(10, 30):
        for y in range(10, 30):
            image.putpixel((x, y), (0, 0, 0) if (x + y) % 2 else (255, 255, 255))

    result = blur_regions(image, [[(10, 10), (29, 10), (29, 29), (10, 29)]])

    assert result.region_count == 1
    assert result.image.getpixel((0, 0)) == image.getpixel((0, 0))
    assert result.image.getpixel((15, 15)) != image.getpixel((15, 15))


def test_validate_polygon_rejects_out_of_bounds_coordinate() -> None:
    with pytest.raises(AppError) as error:
        validate_polygon([(0, 0), (40, 0), (10, 10)], 40, 40)

    assert error.value.code == "REGION_OUT_OF_BOUNDS"


def test_validate_polygon_rejects_self_intersection() -> None:
    with pytest.raises(AppError) as error:
        validate_polygon([(5, 5), (30, 30), (5, 30), (30, 5)], 40, 40)

    assert error.value.code == "INVALID_REGION"
