"""OCR backend result types and implementations."""

from .base import OCRLine, OCRResult, OCRWord, format_deep_ocr_for_prompt
from .tesseract_backend import TesseractOCRBackend
from .unlimited_ocr_backend import UnlimitedOCRBackend

__all__ = [
    "OCRResult",
    "OCRLine",
    "OCRWord",
    "TesseractOCRBackend",
    "UnlimitedOCRBackend",
    "format_deep_ocr_for_prompt",
]
