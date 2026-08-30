from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import random
import statistics
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version

from benchmarks.native_ocr_preprocessing import (
    NativeOCRPreprocessor,
    native_architecture_name,
)
from agetha.platform.screen_monitoring import ProcessedOCRImage, preprocess_ocr_image
from benchmarks.evaluate_native_ocr import (
    BenchmarkCell,
    QualificationDecision,
    evaluate_qualification,
    percentile,
    render_markdown,
)


Preprocessor = Callable[..., ProcessedOCRImage]
BENCHMARK_SIZES = ((640, 360), (1280, 720), (1920, 1080), (3840, 2160))
BENCHMARK_MODES = ("basic", "auto")


def size_bucket(size: tuple[int, int]) -> str | None:
    pixels = int(size[0]) * int(size[1])
    if pixels <= 921_600:
        return "small"
    if pixels <= 2_073_600:
        return "1080p"
    if pixels <= 8_294_400:
        return "4k"
    return None


def make_benchmark_image(size: tuple[int, int]) -> Image.Image:
    width, height = size
    vertical = Image.linear_gradient("L").resize(size)
    horizontal = vertical.transpose(Image.Transpose.TRANSPOSE)
    horizontal = horizontal.resize(size)
    blue = Image.new("L", size, 96)
    image = Image.merge("RGB", (horizontal, vertical, blue))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    step = max(32, height // 8)
    for y in range(step // 2, height, step):
        draw.text(
            (max(8, width // 30), y),
            f"Agetha OCR benchmark {width}x{height} row {y}",
            fill="black",
            font=font,
        )
    return image


def _touch_output(result: ProcessedOCRImage) -> int:
    return int(result.image.getpixel((result.image.width - 1, result.image.height - 1)))


def benchmark_callable(
    preprocess: Preprocessor,
    image: Image.Image,
    *,
    max_dimension: int,
    mode: str,
    warmups: int,
    iterations: int,
) -> tuple[tuple[float, ...], float, int]:
    for _ in range(max(0, warmups)):
        _touch_output(preprocess(
            image, max_dimension=max_dimension, mode=mode, upscale=2,
        ))
    samples: list[float] = []
    failures = 0
    cpu_start = time.process_time()
    for _ in range(max(0, iterations)):
        started = time.perf_counter_ns()
        try:
            result = preprocess(
                image, max_dimension=max_dimension, mode=mode, upscale=2,
            )
            _touch_output(result)
        except Exception:
            failures += 1
            continue
        samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    cpu_seconds = time.process_time() - cpu_start
    if not samples:
        raise RuntimeError("benchmark backend produced no valid samples")
    return tuple(samples), cpu_seconds, failures


def build_cell(
    *,
    architecture: str,
    mode: str,
    size: str,
    backend: str,
    samples_ms: tuple[float, ...],
    process_cpu_seconds: float,
    peak_working_set_bytes: int,
    retained_bytes: int,
    cold_ms: float,
    failures: int,
    parity_passed: bool,
) -> BenchmarkCell:
    return BenchmarkCell(
        architecture=architecture,
        mode=mode,
        size=size,
        backend=backend,
        samples_ms=samples_ms,
        median_ms=statistics.median(samples_ms),
        p95_ms=percentile(samples_ms, 0.95),
        p99_ms=percentile(samples_ms, 0.99),
        process_cpu_seconds=process_cpu_seconds,
        peak_working_set_bytes=peak_working_set_bytes,
        retained_bytes=retained_bytes,
        cold_ms=cold_ms,
        failures=failures,
        parity_passed=parity_passed,
    )


def load_native_backend(
    native_dll: Path,
) -> tuple[NativeOCRPreprocessor, float]:
    started = time.perf_counter_ns()
    processor = NativeOCRPreprocessor.from_library(native_dll)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    return processor, elapsed_ms


def _rss_bytes() -> int:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except Exception:
        return 0


class _MemorySampler:
    def __init__(self):
        self._stop = threading.Event()
        self._baseline = _rss_bytes()
        self._peak = self._baseline
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.002):
            self._peak = max(self._peak, _rss_bytes())

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._peak = max(self._peak, _rss_bytes())

    @property
    def incremental_peak(self) -> int:
        return max(0, self._peak - self._baseline)

    def retained_after_collection(self) -> int:
        gc.collect()
        return max(0, _rss_bytes() - self._baseline)


def _resource_probe(
    preprocess: Preprocessor,
    image: Image.Image,
    *,
    max_dimension: int,
    mode: str,
    iterations: int,
) -> tuple[int, int, int]:
    failures = 0
    expected_digest: bytes | None = None
    with _MemorySampler() as memory:
        for _ in range(iterations):
            try:
                result = preprocess(
                    image, max_dimension=max_dimension, mode=mode, upscale=2,
                )
                digest = hashlib.blake2b(
                    result.image.tobytes(), digest_size=16,
                ).digest()
                if expected_digest is None:
                    expected_digest = digest
                elif digest != expected_digest:
                    failures += 1
                del result
            except Exception:
                failures += 1
    retained = memory.retained_after_collection()
    return memory.incremental_peak, retained, failures


def _cold_call_ms(
    preprocess: Preprocessor,
    image: Image.Image,
    *,
    max_dimension: int,
    mode: str,
) -> float:
    started = time.perf_counter_ns()
    result = preprocess(
        image, max_dimension=max_dimension, mode=mode, upscale=2,
    )
    _touch_output(result)
    return (time.perf_counter_ns() - started) / 1_000_000.0


def _measure_pair(
    python_preprocess: Preprocessor,
    native_preprocess: Preprocessor,
    image: Image.Image,
    *,
    max_dimension: int,
    mode: str,
    warmups: int,
    iterations: int,
) -> dict[str, tuple[tuple[float, ...], float, int]]:
    for preprocess in (python_preprocess, native_preprocess):
        for _ in range(warmups):
            _touch_output(preprocess(
                image, max_dimension=max_dimension, mode=mode, upscale=2,
            ))
    schedule = ["python", "native"] * iterations
    random.Random(38).shuffle(schedule)
    preprocessors = {"python": python_preprocess, "native": native_preprocess}
    samples = {"python": [], "native": []}
    cpu = {"python": 0.0, "native": 0.0}
    failures = {"python": 0, "native": 0}
    for backend in schedule:
        wall_start = time.perf_counter_ns()
        cpu_start = time.process_time()
        try:
            result = preprocessors[backend](
                image, max_dimension=max_dimension, mode=mode, upscale=2,
            )
            _touch_output(result)
        except Exception:
            failures[backend] += 1
            continue
        finally:
            cpu[backend] += time.process_time() - cpu_start
        samples[backend].append(
            (time.perf_counter_ns() - wall_start) / 1_000_000.0,
        )
    if not samples["python"] or not samples["native"]:
        raise RuntimeError("one benchmark backend produced no valid samples")
    return {
        backend: (tuple(samples[backend]), cpu[backend], failures[backend])
        for backend in ("python", "native")
    }


def run_benchmark(
    *,
    native_dll: Path | None,
    iterations: int,
    warmups: int,
    reliability_iterations: int,
    max_dimension: int,
    parity_verified: bool,
) -> tuple[list[BenchmarkCell], list[QualificationDecision]]:
    architecture = native_architecture_name() or (
        f"{platform.system().casefold()}-{platform.machine().casefold()}"
    )
    native_processor = None
    native_load_ms = 0.0
    if native_dll is not None:
        native_processor, native_load_ms = load_native_backend(native_dll)
    cells: list[BenchmarkCell] = []
    decisions: list[QualificationDecision] = []
    for size in BENCHMARK_SIZES:
        bucket = size_bucket(size)
        if bucket is None:
            continue
        image = make_benchmark_image(size)
        case_name = f"{bucket}:{size[0]}x{size[1]}"
        for mode in BENCHMARK_MODES:
            python_cold = _cold_call_ms(
                preprocess_ocr_image,
                image,
                max_dimension=max_dimension,
                mode=mode,
            )
            if native_processor is None:
                samples, cpu, failures = benchmark_callable(
                    preprocess_ocr_image,
                    image,
                    max_dimension=max_dimension,
                    mode=mode,
                    warmups=warmups,
                    iterations=iterations,
                )
                peak, retained, resource_failures = _resource_probe(
                    preprocess_ocr_image,
                    image,
                    max_dimension=max_dimension,
                    mode=mode,
                    iterations=(
                        reliability_iterations if bucket == "small" else 5
                    ),
                )
                cells.append(build_cell(
                    architecture=architecture,
                    mode=mode,
                    size=case_name,
                    backend="python",
                    samples_ms=samples,
                    process_cpu_seconds=cpu,
                    peak_working_set_bytes=peak,
                    retained_bytes=retained,
                    cold_ms=python_cold,
                    failures=failures + resource_failures,
                    parity_passed=True,
                ))
                continue

            native_cold = native_load_ms + _cold_call_ms(
                native_processor.preprocess,
                image,
                max_dimension=max_dimension,
                mode=mode,
            )
            pair = _measure_pair(
                preprocess_ocr_image,
                native_processor.preprocess,
                image,
                max_dimension=max_dimension,
                mode=mode,
                warmups=warmups,
                iterations=iterations,
            )
            pair_cells = {}
            for backend, preprocess, cold_ms in (
                ("python", preprocess_ocr_image, python_cold),
                ("native", native_processor.preprocess, native_cold),
            ):
                samples, cpu, failures = pair[backend]
                peak, retained, resource_failures = _resource_probe(
                    preprocess,
                    image,
                    max_dimension=max_dimension,
                    mode=mode,
                    iterations=(
                        reliability_iterations if bucket == "small" else 5
                    ),
                )
                pair_cells[backend] = build_cell(
                    architecture=architecture,
                    mode=mode,
                    size=case_name,
                    backend=backend,
                    samples_ms=samples,
                    process_cpu_seconds=cpu,
                    peak_working_set_bytes=peak,
                    retained_bytes=retained,
                    cold_ms=cold_ms,
                    failures=failures + resource_failures,
                    parity_passed=(backend == "python" or parity_verified),
                )
                cells.append(pair_cells[backend])
            decisions.append(evaluate_qualification(
                pair_cells["python"], pair_cells["native"],
            ))
    return cells, decisions


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark complete Python/native OCR preprocessing paths",
    )
    parser.add_argument("--native-dll", type=Path)
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--parity-verified", action="store_true")
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--warmups", type=int, default=10)
    parser.add_argument("--reliability-iterations", type=int, default=1000)
    parser.add_argument("--max-dimension", type=int, default=2560)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.python_only and args.native_dll is not None:
        parser.error("--python-only and --native-dll are mutually exclusive")
    if not args.python_only and args.native_dll is None:
        parser.error("--native-dll is required unless --python-only is used")
    if min(args.iterations, args.warmups, args.reliability_iterations) < 0:
        parser.error("iteration counts cannot be negative")
    if args.iterations < 1:
        parser.error("--iterations must be at least one")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    started = time.time()
    cells, decisions = run_benchmark(
        native_dll=args.native_dll,
        iterations=args.iterations,
        warmups=args.warmups,
        reliability_iterations=args.reliability_iterations,
        max_dimension=args.max_dimension,
        parity_verified=args.parity_verified,
    )
    payload = {
        "schema_version": 1,
        "metadata": {
            "python": sys.version,
            "pillow": pillow_version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "iterations": args.iterations,
            "warmups": args.warmups,
            "reliability_iterations": args.reliability_iterations,
            "max_dimension": args.max_dimension,
            "elapsed_seconds": time.time() - started,
            "commit": os.environ.get("GITHUB_SHA", "uncommitted"),
        },
        "cells": [cell.to_dict() for cell in cells],
        "decisions": [decision.to_dict() for decision in decisions],
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    output.with_suffix(".md").write_text(
        render_markdown(cells, decisions), encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
