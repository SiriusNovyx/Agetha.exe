from __future__ import annotations

import unittest

from PIL import Image

from agetha.platform.screen_monitoring import preprocess_ocr_image
from tests.ocr_preprocessing_contract import (
    assert_backend_contract,
    expected_reference,
    make_gradient,
    pixel_parity,
)


class OCRPreprocessingReferenceContractTests(unittest.TestCase):
    def test_python_backend_satisfies_reference_contract(self):
        assert_backend_contract(
            self, preprocess_ocr_image, require_exact_pixels=True,
        )

    def test_dark_auto_mode_preserves_current_inversion_decision(self):
        image = Image.new("RGB", (20, 10), (20, 20, 20))

        processed = preprocess_ocr_image(
            image, max_dimension=100, mode="auto", upscale=1,
        )

        self.assertEqual(processed.image.getextrema(), (235, 235))

    def test_expected_reference_detects_a_pixel_regression(self):
        image = make_gradient((31, 17))
        reference = expected_reference(
            image, max_dimension=20, mode="basic", upscale=2,
        )
        changed = reference.image.copy()
        changed.putpixel((0, 0), (changed.getpixel((0, 0)) + 10) % 256)

        metrics = pixel_parity(reference.image, changed)

        self.assertGreater(metrics.mean_absolute_error, 0.0)
        self.assertEqual(metrics.max_absolute_error, 10)


if __name__ == "__main__":
    unittest.main()
