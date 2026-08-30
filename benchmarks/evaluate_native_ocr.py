from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


MIB = 1024 * 1024
MIN_MEDIAN_IMPROVEMENT = 0.20
MAX_TAIL_RATIO = 1.10
MAX_CPU_RATIO = 1.10
MAX_PEAK_WORKING_SET_DELTA = 32 * MIB
MAX_RETAINED_MEMORY_DELTA = 8 * MIB
MAX_COLD_START_DELTA_MS = 250.0


@dataclass(frozen=True)
class BenchmarkCell:
    architecture: str
    mode: str
    size: str
    backend: str
    samples_ms: tuple[float, ...]
    median_ms: float
    p95_ms: float
    p99_ms: float
    process_cpu_seconds: float
    peak_working_set_bytes: int
    retained_bytes: int
    cold_ms: float
    failures: int
    parity_passed: bool

    @property
    def key(self) -> tuple[str, str, str]:
        return self.architecture, self.mode, self.size

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["samples_ms"] = list(self.samples_ms)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "BenchmarkCell":
        required = {
            "architecture", "mode", "size", "backend", "samples_ms",
            "median_ms", "p95_ms", "p99_ms", "process_cpu_seconds",
            "peak_working_set_bytes", "retained_bytes", "cold_ms",
            "failures", "parity_passed",
        }
        missing = required - data.keys()
        if missing:
            raise ValueError(f"benchmark cell missing fields: {sorted(missing)}")
        return cls(
            architecture=str(data["architecture"]),
            mode=str(data["mode"]),
            size=str(data["size"]),
            backend=str(data["backend"]),
            samples_ms=tuple(float(value) for value in data["samples_ms"]),
            median_ms=float(data["median_ms"]),
            p95_ms=float(data["p95_ms"]),
            p99_ms=float(data["p99_ms"]),
            process_cpu_seconds=float(data["process_cpu_seconds"]),
            peak_working_set_bytes=int(data["peak_working_set_bytes"]),
            retained_bytes=int(data["retained_bytes"]),
            cold_ms=float(data["cold_ms"]),
            failures=int(data["failures"]),
            parity_passed=bool(data["parity_passed"]),
        )


@dataclass(frozen=True)
class QualificationDecision:
    architecture: str
    mode: str
    size: str
    qualified: bool
    median_improvement: float
    reasons: tuple[str, ...]

    @property
    def key(self) -> tuple[str, str, str]:
        return self.architecture, self.mode, self.size

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


def percentile(samples: Iterable[float], probability: float) -> float:
    values = sorted(float(value) for value in samples)
    if not values:
        raise ValueError("percentile requires at least one sample")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be between zero and one")
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def evaluate_qualification(
    python_cell: BenchmarkCell,
    native_cell: BenchmarkCell,
) -> QualificationDecision:
    if python_cell.key != native_cell.key:
        raise ValueError("backends must describe the same benchmark cell")
    if python_cell.backend != "python" or native_cell.backend != "native":
        raise ValueError("qualification requires python then native cells")
    if python_cell.median_ms <= 0:
        raise ValueError("Python median must be positive")

    improvement = 1.0 - native_cell.median_ms / python_cell.median_ms
    reasons: list[str] = []
    if improvement + 1e-12 < MIN_MEDIAN_IMPROVEMENT:
        reasons.append("median_improvement_below_20_percent")
    if native_cell.p95_ms > python_cell.p95_ms * MAX_TAIL_RATIO:
        reasons.append("p95_regression_above_10_percent")
    if native_cell.p99_ms > python_cell.p99_ms * MAX_TAIL_RATIO:
        reasons.append("p99_regression_above_10_percent")
    if not native_cell.parity_passed:
        reasons.append("parity_failed")
    if native_cell.process_cpu_seconds > python_cell.process_cpu_seconds * MAX_CPU_RATIO:
        reasons.append("cpu_regression_above_10_percent")
    if (
        native_cell.peak_working_set_bytes
        > python_cell.peak_working_set_bytes + MAX_PEAK_WORKING_SET_DELTA
    ):
        reasons.append("peak_working_set_regression")
    if (
        native_cell.retained_bytes
        > python_cell.retained_bytes + MAX_RETAINED_MEMORY_DELTA
    ):
        reasons.append("retained_memory_regression")
    if native_cell.cold_ms > python_cell.cold_ms + MAX_COLD_START_DELTA_MS:
        reasons.append("cold_start_regression")
    if native_cell.failures:
        reasons.append("reliability_failure")
    return QualificationDecision(
        architecture=python_cell.architecture,
        mode=python_cell.mode,
        size=python_cell.size,
        qualified=not reasons,
        median_improvement=improvement,
        reasons=tuple(reasons),
    )


def render_markdown(
    cells: Iterable[BenchmarkCell],
    decisions: Iterable[QualificationDecision],
) -> str:
    ordered_cells = sorted(
        cells,
        key=lambda cell: (cell.architecture, cell.mode, cell.size, cell.backend),
    )
    ordered_decisions = sorted(decisions, key=lambda decision: decision.key)
    lines = [
        "# Native OCR preprocessing benchmark",
        "",
        "## Measurements",
        "",
        "| Architecture | Mode | Size | Backend | Median ms | p95 ms | p99 ms | CPU s | Peak MiB | Retained MiB | Cold ms | Failures | Parity |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for cell in ordered_cells:
        lines.append(
            f"| {cell.architecture} | {cell.mode} | {cell.size} | {cell.backend} "
            f"| {cell.median_ms:.3f} | {cell.p95_ms:.3f} | {cell.p99_ms:.3f} "
            f"| {cell.process_cpu_seconds:.3f} "
            f"| {cell.peak_working_set_bytes / MIB:.2f} "
            f"| {cell.retained_bytes / MIB:.2f} | {cell.cold_ms:.3f} "
            f"| {cell.failures} | {'yes' if cell.parity_passed else 'no'} |"
        )
    lines.extend([
        "",
        "## Qualification",
        "",
        "| Architecture | Mode | Size | Improvement | Qualified | Reasons |",
        "|---|---|---|---:|---|---|",
    ])
    for decision in ordered_decisions:
        reasons = ", ".join(decision.reasons) if decision.reasons else "none"
        lines.append(
            f"| {decision.architecture} | {decision.mode} | {decision.size} "
            f"| {decision.median_improvement:.2%} "
            f"| {'yes' if decision.qualified else 'no'} | {reasons} |"
        )
    return "\n".join(lines) + "\n"
