"""Adapter around Agetha's existing pytesseract word extraction."""

from __future__ import annotations

import math

from .base import OCRLine, OCRResult, OCRWord


class TesseractOCRBackend:
    """Run OCR on an already-preprocessed image without changing capture policy."""

    name = "tesseract"

    def __init__(self, pytesseract_module):
        self._pytesseract = pytesseract_module

    def analyze(
        self,
        image,
        *,
        capture_left: int = 0,
        capture_top: int = 0,
        scale: float = 1.0,
        processing_scale_x: float | None = None,
        processing_scale_y: float | None = None,
        max_chars: int = 3000,
        min_word_confidence: float = 30.0,
        languages: str = "eng",
        psm: int = 3,
    ) -> OCRResult:
        if self._pytesseract is None:
            return OCRResult(
                text="",
                words=[],
                backend=self.name,
                metadata={"status": "unavailable", "error": "tesseract_unavailable"},
            )

        scale_x = max(0.000001, float(processing_scale_x or scale))
        scale_y = max(0.000001, float(processing_scale_y or scale))
        config = f"--psm {int(psm)}"
        try:
            data = self._pytesseract.image_to_data(
                image,
                lang=languages,
                config=config,
                output_type=self._pytesseract.Output.DICT,
            )
            words: list[OCRWord] = []
            grouped: dict[
                tuple[int, int, int, int], list[tuple[int, OCRWord]]
            ] = {}
            for index in range(len(data["text"])):
                text = str(data["text"][index]).strip()
                # pytesseract versions vary between integer and decimal strings.
                confidence = float(data["conf"][index])
                if not text or not math.isfinite(confidence) or confidence <= 0:
                    continue
                word = OCRWord(
                    text=text,
                    x=int(capture_left) + round(float(data["left"][index]) / scale_x),
                    y=int(capture_top) + round(float(data["top"][index]) / scale_y),
                    width=round(float(data["width"][index]) / scale_x),
                    height=round(float(data["height"][index]) / scale_y),
                    confidence=confidence,
                )
                key = tuple(
                    int(data.get(field, [1] * len(data["text"]))[index])
                    for field in ("page_num", "block_num", "par_num", "line_num")
                )
                word_number = int(
                    data.get("word_num", list(range(len(data["text"]))))[index]
                )
                grouped.setdefault(key, []).append((word_number, word))
                if confidence >= float(min_word_confidence):
                    words.append(word)

            lines: list[OCRLine] = []
            for key in sorted(grouped):
                line_words = [
                    word for _number, word in sorted(
                        grouped[key], key=lambda pair: pair[0],
                    )
                ]
                left = min(word.x for word in line_words)
                top = min(word.y for word in line_words)
                right = max(word.x + word.width for word in line_words)
                bottom = max(word.y + word.height for word in line_words)
                lines.append(OCRLine(
                    text=" ".join(word.text for word in line_words),
                    x=left,
                    y=top,
                    width=max(0, right - left),
                    height=max(0, bottom - top),
                    average_confidence=(
                        sum(float(word.confidence or 0.0) for word in line_words)
                        / len(line_words)
                    ),
                    words=line_words,
                ))
            plain_text = "\n".join(line.text for line in lines)
        except Exception:
            plain_text = self._pytesseract.image_to_string(
                image, lang=languages, config=config,
            )
            words = []
            lines = []

        normalized_text = "\n".join(
            line.strip() for line in plain_text.splitlines() if line.strip()
        )[:max(0, int(max_chars))]
        return OCRResult(
            text=normalized_text,
            words=words,
            backend=self.name,
            lines=lines,
            metadata={
                "processing_scale_x": scale_x,
                "processing_scale_y": scale_y,
                "languages": languages,
                "psm": int(psm),
            },
        )
