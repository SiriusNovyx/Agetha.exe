from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

from agetha.platform.screen_monitoring import ProcessedOCRImage


BASIC_MAX_MEAN_ABS_ERROR = 4.0
BASIC_MAX_P99_ABS_ERROR = 24.0
AUTO_MAX_MEAN_ABS_ERROR = 6.0
AUTO_MAX_P99_ABS_ERROR = 32.0
OCR_MIN_TOKEN_RECALL = 0.98
OCR_MAX_MEDIAN_CONFIDENCE_DELTA = 5.0
OCR_MIN_MATCHED_BOX_IOU = 0.85
OCR_MAX_SOURCE_CENTER_DELTA_PX = 3.0


class PreprocessCallable(Protocol):
    def __call__(
        self,
        image: Image.Image,
        *,
        max_dimension: int,
        mode: str = "auto",
        upscale: int = 2,
    ) -> ProcessedOCRImage: ...


@dataclass(frozen=True)
class PixelParity:
    mean_absolute_error: float
    p99_absolute_error: float
    max_absolute_error: int


def make_gradient(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (
                (x * 17 + y * 3) % 256,
                (x * 5 + y * 19) % 256,
                (x * 11 + y * 7) % 256,
            )
    return image


def make_text_image(
    size: tuple[int, int],
    *,
    foreground: str = "black",
    background: str = "white",
) -> Image.Image:
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    draw.text((max(2, size[0] // 20), max(2, size[1] // 4)),
              "Agetha OCR 123", fill=foreground)
    draw.rectangle(
        (size[0] // 3, size[1] // 2, size[0] * 2 // 3, size[1] * 3 // 4),
        outline=foreground,
        width=max(1, min(size) // 80),
    )
    return image


def representative_images() -> dict[str, Image.Image]:
    return {
        "gradient": make_gradient((127, 73)),
        "high_contrast": make_text_image((320, 180)),
        "low_contrast": make_text_image(
            (320, 180), foreground="#777777", background="#aaaaaa",
        ),
        "dark": make_text_image(
            (320, 180), foreground="#202020", background="#080808",
        ),
        "one_pixel_axis": make_gradient((1, 19)),
    }


def expected_reference(
    image: Image.Image,
    *,
    max_dimension: int,
    mode: str,
    upscale: int,
) -> ProcessedOCRImage:
    original_width, original_height = image.size
    resized = image
    if max(original_width, original_height) > max_dimension:
        ratio = float(max_dimension) / max(original_width, original_height)
        resized = image.resize(
            (
                max(1, round(original_width * ratio)),
                max(1, round(original_height * ratio)),
            ),
            Image.Resampling.LANCZOS,
        )
    processed = resized.resize(
        (max(1, resized.width * upscale), max(1, resized.height * upscale)),
        Image.Resampling.LANCZOS,
    ).convert("L")
    if mode == "auto":
        processed = ImageOps.autocontrast(processed)
        if float(ImageStat.Stat(processed).mean[0]) < 70.0:
            processed = ImageOps.invert(processed)
        processed = processed.filter(ImageFilter.SHARPEN)
    return ProcessedOCRImage(
        image=processed,
        scale_x=processed.width / max(1, original_width),
        scale_y=processed.height / max(1, original_height),
    )


def pixel_parity(reference: Image.Image, candidate: Image.Image) -> PixelParity:
    if reference.mode != "L" or candidate.mode != "L":
        raise ValueError("pixel parity requires grayscale images")
    if reference.size != candidate.size:
        raise ValueError("pixel parity requires equal dimensions")
    differences = sorted(
        abs(left - right)
        for left, right in zip(reference.getdata(), candidate.getdata())
    )
    if not differences:
        return PixelParity(0.0, 0.0, 0)
    p99_index = min(len(differences) - 1, math.ceil(len(differences) * 0.99) - 1)
    return PixelParity(
        mean_absolute_error=sum(differences) / len(differences),
        p99_absolute_error=float(differences[p99_index]),
        max_absolute_error=differences[-1],
    )


def assert_backend_contract(
    testcase,
    preprocess: PreprocessCallable,
    *,
    require_exact_pixels: bool = False,
) -> None:
    for name, image in representative_images().items():
        for mode in ("basic", "auto"):
            with testcase.subTest(image=name, mode=mode):
                before_mode = image.mode
                before_bytes = image.tobytes()
                actual = preprocess(
                    image,
                    max_dimension=96,
                    mode=mode,
                    upscale=2,
                )
                expected = expected_reference(
                    image,
                    max_dimension=96,
                    mode=mode,
                    upscale=2,
                )
                testcase.assertEqual(actual.image.mode, "L")
                testcase.assertEqual(actual.image.size, expected.image.size)
                if require_exact_pixels:
                    testcase.assertEqual(
                        actual.image.tobytes(), expected.image.tobytes(),
                    )
                else:
                    testcase.assertEqual(actual.image.getextrema()[0] >= 0, True)
                    testcase.assertEqual(actual.image.getextrema()[1] <= 255, True)
                testcase.assertAlmostEqual(actual.scale_x, expected.scale_x)
                testcase.assertAlmostEqual(actual.scale_y, expected.scale_y)
                testcase.assertEqual(image.mode, before_mode)
                testcase.assertEqual(image.tobytes(), before_bytes)

    odd = make_gradient((101, 51))
    odd_result = preprocess(odd, max_dimension=50, mode="basic", upscale=2)
    testcase.assertEqual(odd_result.image.size, (100, 50))
    testcase.assertAlmostEqual(odd_result.scale_x, 100 / 101)
    testcase.assertAlmostEqual(odd_result.scale_y, 50 / 51)

    unknown_mode = preprocess(odd, max_dimension=200, mode="unknown", upscale=2)
    basic_mode = preprocess(odd, max_dimension=200, mode="basic", upscale=2)
    testcase.assertEqual(unknown_mode.image.tobytes(), basic_mode.image.tobytes())

    clamped = preprocess(odd, max_dimension=0, mode="basic", upscale=0)
    testcase.assertEqual(clamped.image.size, (1, 1))
