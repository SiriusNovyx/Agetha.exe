from __future__ import annotations

import os
import re
import statistics
import unittest
from collections import Counter, defaultdict, deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from benchmarks.native_ocr_preprocessing import NativeOCRPreprocessor
from agetha.platform.screen_monitoring import preprocess_ocr_image
from tests.ocr_preprocessing_contract import (
    AUTO_MAX_MEAN_ABS_ERROR,
    AUTO_MAX_P99_ABS_ERROR,
    BASIC_MAX_MEAN_ABS_ERROR,
    BASIC_MAX_P99_ABS_ERROR,
    OCR_MAX_MEDIAN_CONFIDENCE_DELTA,
    OCR_MAX_SOURCE_CENTER_DELTA_PX,
    OCR_MIN_MATCHED_BOX_IOU,
    OCR_MIN_TOKEN_RECALL,
    assert_backend_contract,
    pixel_parity,
    representative_images,
)


def _native_dll_path() -> Path | None:
    raw = os.environ.get("AGETHA_NATIVE_OCR_DLL", "").strip()
    return Path(raw).resolve() if raw else None


def _font_path() -> Path | None:
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _ocr_fixture() -> tuple[Image.Image, Path]:
    font_path = _font_path()
    if font_path is None:
        raise unittest.SkipTest("deterministic TrueType OCR fixture font unavailable")
    image = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 54)
    lines = (
        "Agetha native OCR parity 12345",
        "Error details remain readable",
        "Coordinates map to source pixels",
    )
    for index, line in enumerate(lines):
        draw.text((70, 90 + index * 150), line, font=font, fill="black")
    return image, font_path


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _ocr_words(image: Image.Image) -> list[dict[str, float | str]]:
    try:
        import pytesseract
        from pytesseract import Output

        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise unittest.SkipTest(f"Tesseract executable unavailable: {exc}") from exc
    data = pytesseract.image_to_data(
        image, lang="eng", config="--psm 6", output_type=Output.DICT,
    )
    words: list[dict[str, float | str]] = []
    for index, raw_text in enumerate(data["text"]):
        token = _normalize_token(raw_text)
        if not token:
            continue
        words.append({
            "token": token,
            "confidence": float(data["conf"][index]),
            "left": float(data["left"][index]),
            "top": float(data["top"][index]),
            "width": float(data["width"][index]),
            "height": float(data["height"][index]),
        })
    return words


def _source_box(word: dict[str, float | str], scale_x: float, scale_y: float):
    left = float(word["left"]) / scale_x
    top = float(word["top"]) / scale_y
    right = left + float(word["width"]) / scale_x
    bottom = top + float(word["height"]) / scale_y
    return left, top, right, bottom


def _box_iou(left, right) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 1.0


class NativeOCRPreprocessingParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = _native_dll_path()
        cls.native = NativeOCRPreprocessor.from_library(path) if path else None

    def require_native(self) -> NativeOCRPreprocessor:
        if self.native is None:
            self.skipTest("native OCR DLL not supplied")
        return self.native

    def test_ci_requirement_cannot_silently_skip_native(self):
        if os.environ.get("AGETHA_REQUIRE_NATIVE_OCR") == "1":
            self.assertIsNotNone(self.native, "CI required a real native OCR DLL")

    def test_native_backend_satisfies_shared_contract(self):
        native = self.require_native()

        assert_backend_contract(self, native.preprocess)

    def test_pixel_parity_stays_within_predetermined_thresholds(self):
        native = self.require_native()
        for fixture_name, image in representative_images().items():
            for mode in ("basic", "auto"):
                with self.subTest(fixture=fixture_name, mode=mode):
                    reference = preprocess_ocr_image(
                        image, max_dimension=96, mode=mode, upscale=2,
                    )
                    candidate = native.preprocess(
                        image, max_dimension=96, mode=mode, upscale=2,
                    )
                    metrics = pixel_parity(reference.image, candidate.image)
                    mean_limit = (
                        AUTO_MAX_MEAN_ABS_ERROR
                        if mode == "auto" else BASIC_MAX_MEAN_ABS_ERROR
                    )
                    p99_limit = (
                        AUTO_MAX_P99_ABS_ERROR
                        if mode == "auto" else BASIC_MAX_P99_ABS_ERROR
                    )
                    self.assertLessEqual(metrics.mean_absolute_error, mean_limit)
                    self.assertLessEqual(metrics.p99_absolute_error, p99_limit)

    def test_ocr_text_confidence_and_source_coordinates_remain_equivalent(self):
        native = self.require_native()
        image, font_path = _ocr_fixture()
        for mode in ("basic", "auto"):
            with self.subTest(mode=mode, font=font_path.name):
                reference = preprocess_ocr_image(
                    image, max_dimension=1280, mode=mode, upscale=2,
                )
                candidate = native.preprocess(
                    image, max_dimension=1280, mode=mode, upscale=2,
                )
                reference_words = _ocr_words(reference.image)
                candidate_words = _ocr_words(candidate.image)
                reference_tokens = Counter(
                    str(word["token"]) for word in reference_words
                )
                candidate_tokens = Counter(
                    str(word["token"]) for word in candidate_words
                )
                overlap = sum((reference_tokens & candidate_tokens).values())
                recall = overlap / max(1, sum(reference_tokens.values()))
                self.assertGreaterEqual(recall, OCR_MIN_TOKEN_RECALL)

                candidate_by_token = defaultdict(deque)
                for word in candidate_words:
                    candidate_by_token[str(word["token"])].append(word)
                confidence_deltas = []
                coordinate_passes = 0
                matched = 0
                for reference_word in reference_words:
                    token = str(reference_word["token"])
                    if not candidate_by_token[token]:
                        continue
                    candidate_word = candidate_by_token[token].popleft()
                    matched += 1
                    confidence_deltas.append(abs(
                        float(reference_word["confidence"])
                        - float(candidate_word["confidence"])
                    ))
                    reference_box = _source_box(
                        reference_word, reference.scale_x, reference.scale_y,
                    )
                    candidate_box = _source_box(
                        candidate_word, candidate.scale_x, candidate.scale_y,
                    )
                    reference_center = (
                        (reference_box[0] + reference_box[2]) / 2,
                        (reference_box[1] + reference_box[3]) / 2,
                    )
                    candidate_center = (
                        (candidate_box[0] + candidate_box[2]) / 2,
                        (candidate_box[1] + candidate_box[3]) / 2,
                    )
                    center_delta = max(
                        abs(reference_center[0] - candidate_center[0]),
                        abs(reference_center[1] - candidate_center[1]),
                    )
                    if (
                        _box_iou(reference_box, candidate_box)
                        >= OCR_MIN_MATCHED_BOX_IOU
                        and center_delta <= OCR_MAX_SOURCE_CENTER_DELTA_PX
                    ):
                        coordinate_passes += 1

                self.assertGreater(matched, 0)
                self.assertLessEqual(
                    statistics.median(confidence_deltas),
                    OCR_MAX_MEDIAN_CONFIDENCE_DELTA,
                )
                self.assertGreaterEqual(coordinate_passes / matched, 0.95)


if __name__ == "__main__":
    unittest.main()
