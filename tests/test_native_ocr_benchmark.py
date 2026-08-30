from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.evaluate_native_ocr import (
    BenchmarkCell,
    evaluate_qualification,
    percentile,
    render_markdown,
)
from benchmarks.ocr_preprocessing import (
    benchmark_callable,
    build_cell,
    make_benchmark_image,
    load_native_backend,
    size_bucket,
)
from agetha.platform.screen_monitoring import ProcessedOCRImage
from PIL import Image


def _cell(
    *,
    backend: str,
    median_ms: float,
    p95_ms: float | None = None,
    p99_ms: float | None = None,
    cpu: float = 1.0,
    peak: int = 100 * 1024 * 1024,
    retained: int = 2 * 1024 * 1024,
    cold_ms: float = 100.0,
    failures: int = 0,
    parity: bool = True,
) -> BenchmarkCell:
    return BenchmarkCell(
        architecture="win-amd64",
        mode="basic",
        size="1080p",
        backend=backend,
        samples_ms=(median_ms,),
        median_ms=median_ms,
        p95_ms=median_ms if p95_ms is None else p95_ms,
        p99_ms=median_ms if p99_ms is None else p99_ms,
        process_cpu_seconds=cpu,
        peak_working_set_bytes=peak,
        retained_bytes=retained,
        cold_ms=cold_ms,
        failures=failures,
        parity_passed=parity,
    )


class NativeOCRBenchmarkEvaluatorTests(unittest.TestCase):
    def test_size_buckets_fail_closed_above_4k(self):
        self.assertEqual(size_bucket((640, 360)), "small")
        self.assertEqual(size_bucket((1280, 720)), "small")
        self.assertEqual(size_bucket((1920, 1080)), "1080p")
        self.assertEqual(size_bucket((3840, 2160)), "4k")
        self.assertIsNone(size_bucket((7680, 4320)))

    def test_benchmark_image_is_deterministic_rgb(self):
        first = make_benchmark_image((320, 180))
        second = make_benchmark_image((320, 180))

        self.assertEqual(first.mode, "RGB")
        self.assertEqual(first.tobytes(), second.tobytes())

    def test_benchmark_callable_measures_every_complete_call(self):
        calls = []

        def preprocess(image, *, max_dimension, mode, upscale):
            calls.append((image.size, max_dimension, mode, upscale))
            return ProcessedOCRImage(Image.new("L", (2, 2), len(calls)), 1.0, 1.0)

        samples, cpu_seconds, failures = benchmark_callable(
            preprocess,
            Image.new("RGB", (1, 1), "white"),
            max_dimension=1,
            mode="basic",
            warmups=2,
            iterations=3,
        )

        self.assertEqual(len(calls), 5)
        self.assertEqual(len(samples), 3)
        self.assertGreaterEqual(cpu_seconds, 0.0)
        self.assertEqual(failures, 0)

    def test_build_cell_derives_percentiles_from_raw_samples(self):
        cell = build_cell(
            architecture="win-amd64",
            mode="auto",
            size="small",
            backend="python",
            samples_ms=(4.0, 1.0, 3.0, 2.0),
            process_cpu_seconds=0.5,
            peak_working_set_bytes=10,
            retained_bytes=2,
            cold_ms=5.0,
            failures=0,
            parity_passed=True,
        )

        self.assertEqual(cell.median_ms, 2.5)
        self.assertAlmostEqual(cell.p95_ms, 3.85)
        self.assertAlmostEqual(cell.p99_ms, 3.97)

    def test_native_load_time_is_measured_separately(self):
        sentinel = object()
        with (
            patch(
                "benchmarks.ocr_preprocessing.NativeOCRPreprocessor.from_library",
                return_value=sentinel,
            ) as load,
            patch(
                "benchmarks.ocr_preprocessing.time.perf_counter_ns",
                side_effect=(1_000_000_000, 1_125_000_000),
            ),
        ):
            processor, load_ms = load_native_backend(Path("C:/test/native.dll"))

        self.assertIs(processor, sentinel)
        self.assertEqual(load_ms, 125.0)
        load.assert_called_once_with(Path("C:/test/native.dll"))

    def test_percentile_interpolates_sorted_samples(self):
        self.assertEqual(percentile((40.0, 10.0, 30.0, 20.0), 0.0), 10.0)
        self.assertEqual(percentile((40.0, 10.0, 30.0, 20.0), 1.0), 40.0)
        self.assertAlmostEqual(
            percentile(tuple(float(value) for value in range(1, 101)), 0.95),
            95.05,
        )

    def test_twenty_percent_median_improvement_qualifies(self):
        decision = evaluate_qualification(
            _cell(backend="python", median_ms=100.0, p95_ms=110.0,
                  p99_ms=115.0, cold_ms=100.0),
            _cell(backend="native", median_ms=80.0, p95_ms=100.0,
                  p99_ms=110.0, cold_ms=300.0),
        )

        self.assertTrue(decision.qualified)
        self.assertAlmostEqual(decision.median_improvement, 0.20)
        self.assertEqual(decision.reasons, ())

    def test_small_median_improvement_is_rejected(self):
        decision = evaluate_qualification(
            _cell(backend="python", median_ms=100.0),
            _cell(backend="native", median_ms=81.0),
        )

        self.assertFalse(decision.qualified)
        self.assertIn("median_improvement_below_20_percent", decision.reasons)

    def test_tail_latency_regression_is_rejected(self):
        decision = evaluate_qualification(
            _cell(backend="python", median_ms=100.0, p95_ms=100.0, p99_ms=100.0),
            _cell(backend="native", median_ms=70.0, p95_ms=111.0, p99_ms=111.0),
        )

        self.assertIn("p95_regression_above_10_percent", decision.reasons)
        self.assertIn("p99_regression_above_10_percent", decision.reasons)

    def test_parity_failure_is_rejected(self):
        decision = evaluate_qualification(
            _cell(backend="python", median_ms=100.0),
            _cell(backend="native", median_ms=70.0, parity=False),
        )

        self.assertIn("parity_failed", decision.reasons)

    def test_cpu_memory_cold_and_reliability_gates_are_enforced(self):
        mib = 1024 * 1024
        decision = evaluate_qualification(
            _cell(backend="python", median_ms=100.0, cpu=1.0,
                  peak=100 * mib, retained=2 * mib, cold_ms=100.0),
            _cell(backend="native", median_ms=70.0, cpu=1.11,
                  peak=133 * mib, retained=11 * mib, cold_ms=351.0,
                  failures=1),
        )

        self.assertIn("cpu_regression_above_10_percent", decision.reasons)
        self.assertIn("peak_working_set_regression", decision.reasons)
        self.assertIn("retained_memory_regression", decision.reasons)
        self.assertIn("cold_start_regression", decision.reasons)
        self.assertIn("reliability_failure", decision.reasons)

    def test_mismatched_cells_fail_closed(self):
        native = _cell(backend="native", median_ms=70.0)
        native = BenchmarkCell(**{
            **native.to_dict(),
            "architecture": "win-arm64",
            "samples_ms": native.samples_ms,
        })

        with self.assertRaisesRegex(ValueError, "same benchmark cell"):
            evaluate_qualification(_cell(backend="python", median_ms=100.0), native)

    def test_json_and_markdown_render_deterministically(self):
        python_cell = _cell(backend="python", median_ms=100.0)
        native_cell = _cell(backend="native", median_ms=70.0)
        decision = evaluate_qualification(python_cell, native_cell)

        first = render_markdown([native_cell, python_cell], [decision])
        second = render_markdown([python_cell, native_cell], [decision])

        self.assertEqual(first, second)
        self.assertIn("win-amd64", first)
        self.assertIn("30.00%", first)
        json.dumps(python_cell.to_dict(), sort_keys=True)


if __name__ == "__main__":
    unittest.main()
