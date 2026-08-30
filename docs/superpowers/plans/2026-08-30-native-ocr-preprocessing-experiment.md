# Native OCR Preprocessing Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine, with architecture-specific end-to-end evidence, whether an optional Windows native OCR-preprocessing backend improves Agetha's `basic` or `auto` preprocessing latency by at least 20% without material correctness, resource, startup, or reliability regressions.

**Architecture:** Preserve `agetha.platform.screen_monitoring.preprocess_ocr_image()` as the reference and mandatory fallback. Add a lazy, versioned `ctypes` loader for a caller-buffer C ABI; implement only approved-image preprocessing in a small WIC-backed DLL; exercise both backends through one contract and benchmark harness. Production selection is added only for architecture/mode/input-size cells that pass the predetermined gate. If none pass, remove the production selection surface and retain only reproducible benchmark evidence.

**Tech Stack:** Python 3.13, Pillow, `ctypes`, C++17, Windows Imaging Component (WIC), CMake/MSVC, `unittest`, `pytesseract`, GitHub Actions Windows AMD64/ARM64 and Ubuntu import/fallback jobs.

**Spec:** `docs/superpowers/specs/2026-08-30-native-ocr-preprocessing-experiment-design.md`

## Global Constraints

- The existing Pillow path remains the behavioral reference and cannot be deleted.
- Native code receives only already-approved RGB pixel data, dimensions, strides, output buffers, and preprocessing parameters.
- OCR policy, capture selection, exclusions, privacy, capability policy, AI behavior, and Tesseract invocation remain in Python.
- The DLL is lazy and optional. Import and startup must work when it is absent, malformed, wrong-architecture, or unloadable.
- The loader resolves only a repository/package-controlled absolute path; it does not search `PATH` or the working directory.
- Every native error, ABI mismatch, unsupported cell, and call failure falls back to Pillow for the same logical request.
- No state from native image contents is logged or persisted.
- Performance qualification is per architecture, preprocessing mode, and size class. An unqualified cell never uses native merely because another cell passed.
- A cell qualifies only with at least 20% warm end-to-end median improvement, no worse than 10% p95/p99 regression, parity within the thresholds below, and no material CPU/memory/reliability regression.
- Normalize runtime architecture names to `win-amd64` and `win-arm64`. Use source-image pixel-count buckets: `small` <= 921,600 pixels, `1080p` <= 2,073,600 pixels, and `4k` <= 8,294,400 pixels. Inputs above 4K remain unqualified.
- A size bucket qualifies only if every benchmark size in that bucket passes; 640x360 and 1280x720 therefore both protect the `small` bucket.
- Predetermined resource gates are: native CPU seconds per warm iteration <= 110% of Python; native incremental peak working set <= Python plus 32 MiB; retained growth after the 1,000-call loop <= Python plus 8 MiB; zero call failures/output drift; and DLL-load-plus-first-call overhead <= the Python first call plus 250 ms.
- A negative benchmark result is a successful experiment. Do not preserve a production native setting or selection path if no cell qualifies.
- No generated DLLs, build directories, benchmark scratch images, API secrets, local venv content, or OCR output enter Git.
- Do not commit or push implementation work without the user's explicit Git authorization. GitHub CI publication is a separate approval checkpoint.

## Execution Status

- [x] Tasks 1-6 implemented with their local test-first checkpoints.
- [x] Focused OCR and authority/security regressions pass locally.
- [x] Compile/import, generated-document, workflow-parse, and diff checks pass.
- [ ] Real DLL build, load, parity, and benchmark evidence await authorized Windows AMD64/ARM64 GitHub CI.
- [x] Full discovery passed outside the restricted sandbox: 1,168 tests, 11 skips. The in-sandbox run reproduced the known Python 3.13 protected-temp ACL limitation; affected production tests were not changed.
- [ ] No implementation commit, push, PR, or remote workflow run has occurred.

---

## Task 1: Lock the Reference Contract and Predetermined Quality Gates

**Files:**

- Create: `tests/test_ocr_preprocessing_contract.py`
- Create: `tests/ocr_preprocessing_contract.py`
- Modify: `tests/test_screen_monitoring_reliability.py`
- Test: `tests/test_ocr_preprocessing_contract.py`

- [ ] **Step 1: Add reusable deterministic input builders and contract assertions**

Create `tests/ocr_preprocessing_contract.py` with builders for RGB gradient, high-contrast text, low-contrast text, dark-background text, odd dimensions, and 640x360, 1920x1080, and 3840x2160 images. Keep Tesseract execution separate from pixel-contract assertions so the ordinary suite remains deterministic when the executable is unavailable.

Use this public test helper shape:

```python
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from agetha.platform.screen_monitoring import ProcessedOCRImage

PreprocessCallable = Callable[..., ProcessedOCRImage]


@dataclass(frozen=True)
class PixelParity:
    mean_absolute_error: float
    p99_absolute_error: float
    max_absolute_error: int


def assert_backend_contract(testcase, preprocess: PreprocessCallable) -> None:
    """Exercise dimensions, grayscale output, scale mapping, modes, and errors."""
```

The contract must assert:

- output mode is `L`;
- dimensions match the current two-stage resize calculation exactly;
- `scale_x` and `scale_y` are derived from final output/source dimensions;
- `basic` performs resize plus grayscale only;
- `auto` includes contrast normalization, the `<70.0` mean inversion decision, and sharpening;
- input image bytes and mode are not mutated;
- invalid `max_dimension`, `upscale`, and mode behavior matches the reference function;
- odd dimensions and one-pixel axes are safe.

- [ ] **Step 2: Write the backend-independent tests against the current Python function**

In `tests/test_ocr_preprocessing_contract.py`, call `assert_backend_contract()` with `preprocess_ocr_image`. Add explicit regression assertions for current rounding order and dark-image inversion.

- [ ] **Step 3: Run the focused test and confirm it exposes any unspecified reference behavior**

Run:

```powershell
python -m unittest tests.test_ocr_preprocessing_contract tests.test_screen_monitoring_reliability -v
```

Expected: tests pass against the unchanged Python implementation. If a proposed contract disagrees with current behavior, correct the contract before native work; do not alter production behavior in this task.

- [ ] **Step 4: Encode parity thresholds before seeing native results**

Add named constants to the helper:

```python
BASIC_MAX_MEAN_ABS_ERROR = 4.0
BASIC_MAX_P99_ABS_ERROR = 24.0
AUTO_MAX_MEAN_ABS_ERROR = 6.0
AUTO_MAX_P99_ABS_ERROR = 32.0
OCR_MIN_TOKEN_RECALL = 0.98
OCR_MAX_MEDIAN_CONFIDENCE_DELTA = 5.0
OCR_MIN_MATCHED_BOX_IOU = 0.85
OCR_MAX_SOURCE_CENTER_DELTA_PX = 3.0
```

Pixel thresholds are screening gates, not permission to ignore OCR regressions. OCR parity later requires normalized token recall of at least 0.98, median word-confidence difference no greater than five percentage points, and at least 95% of matched words satisfying both box IoU and source-coordinate center-delta limits.

- [ ] **Step 5: Inspect the diff and record the reference-only baseline**

Run `git diff --check` and confirm no production file changed.

---

## Task 2: Add the Lazy, Fail-Closed Python ABI Boundary

**Files:**

- Create: `agetha/platform/native_ocr_preprocessing.py`
- Create: `tests/test_native_ocr_preprocessing.py`
- Test: `tests/test_native_ocr_preprocessing.py`

- [ ] **Step 1: Write failing loader and ABI-layout tests using a fake library object**

Test all of these before implementation:

- importing the module on Linux does not call `ctypes.WinDLL`;
- no DLL loads at module import time;
- the default resolver returns only the package-controlled absolute architecture path;
- unsupported OS/architecture yields an unavailable status without raising;
- ABI version and struct-size mismatches reject the library;
- successful calls use contiguous RGB input and a caller-owned grayscale output buffer;
- native nonzero status, exception, short write, or invalid dimensions raises `NativePreprocessError` for the wrapper to handle;
- no raw image bytes appear in status or exception text;
- `Image.frombuffer()` output retains a safe owner for as long as the PIL image is used.

The fake library must expose callable test doubles for the same exported names as the C ABI, and each test must assert call counts so it cannot pass without traversing the intended seam. The only injection seam is the private classmethod `NativeOCRPreprocessor._from_loaded_library_for_tests(library: object, library_path: Path) -> NativeOCRPreprocessor`.

- [ ] **Step 2: Run the loader test and verify RED**

Run:

```powershell
python -m unittest tests.test_native_ocr_preprocessing -v
```

Expected: import failure because `agetha.platform.native_ocr_preprocessing` does not exist.

- [ ] **Step 3: Implement fixed-width request/result structures and status types**

Implement these public Python types:

```python
ABI_VERSION = 1


class NativePreprocessStatus(enum.IntEnum):
    OK = 0
    INVALID_ARGUMENT = 1
    ABI_MISMATCH = 2
    OUTPUT_TOO_SMALL = 3
    COM_INITIALIZATION_FAILED = 4
    WIC_FAILURE = 5
    INTERNAL_ERROR = 6


class NativePreprocessError(RuntimeError):
    def __init__(self, status: NativePreprocessStatus, operation: str):
        super().__init__(f"native OCR preprocessing failed during {operation}: {status.name}")
        self.status = status
        self.operation = operation


class NativeRequestV1(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("input_rgb", ctypes.POINTER(ctypes.c_uint8)),
        ("input_length", ctypes.c_uint64),
        ("input_width", ctypes.c_uint32),
        ("input_height", ctypes.c_uint32),
        ("input_stride", ctypes.c_uint32),
        ("intermediate_width", ctypes.c_uint32),
        ("intermediate_height", ctypes.c_uint32),
        ("output_width", ctypes.c_uint32),
        ("output_height", ctypes.c_uint32),
        ("mode", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 7),
    ]


class NativeResultV1(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("status", ctypes.c_int32),
        ("bytes_written", ctypes.c_uint64),
        ("reserved", ctypes.c_uint32 * 8),
    ]
```

- [ ] **Step 4: Implement explicit library construction and absolute-path resolution**

Expose one intentional public construction path, `NativeOCRPreprocessor.from_library(library_path: Path) -> NativeOCRPreprocessor`, and this preprocessing method:

```python
@dataclass(frozen=True)
class NativeBackendAvailability:
    available: bool
    architecture: str
    reason: str
    library_path: Path | None = None
```

The concrete `preprocess()` signature remains keyword-compatible with the reference: `preprocess(image: Image.Image, *, max_dimension: int, mode: str = "auto", upscale: int = 2) -> ProcessedOCRImage`.

`from_library()` must resolve the exact supplied path before loading, require a regular file, load with Windows safe-search flags, verify ABI/version/structure sizes, and bind `argtypes`/`restype`. The production resolver separately guarantees that its supplied path is inside the expected package-owned native root. Tests may inject a fake library only through `_from_loaded_library_for_tests()`, not a second public constructor.

- [ ] **Step 5: Implement conversion and safe zero-copy reconstruction**

Normalize once with `image.convert("RGB")`, call `tobytes()`, allocate a `bytearray(output_width * output_height)`, pass both caller-owned buffers, validate `bytes_written`, and reconstruct with `Image.frombuffer("L", size, output_buffer, "raw", "L", 0, 1)`.

Attach the output owner through a private result wrapper until tests prove Pillow retains the buffer safely. If that guarantee cannot be proven, use `Image.frombytes()` and count its copy in every benchmark instead of relying on unsafe lifetime behavior.

- [ ] **Step 6: Run focused tests and import smoke**

Run:

```powershell
python -m unittest tests.test_native_ocr_preprocessing -v
python -c "import agetha.platform.native_ocr_preprocessing"
```

Expected: all tests pass without a DLL present and import performs no load.

- [ ] **Step 7: Inspect the diff**

Confirm the new module contains mechanics only: no config, capture, authority, OCR invocation, logging of pixels, or provider/application policy.

---

## Task 3: Define the Versioned C ABI and Build Both Windows Architectures

**Files:**

- Create: `native/ocr_preprocessing/CMakeLists.txt`
- Create: `native/ocr_preprocessing/include/agetha_ocr_preprocessing.h`
- Create: `native/ocr_preprocessing/src/agetha_ocr_preprocessing.cpp`
- Create: `native/ocr_preprocessing/tests/abi_contract.cpp`
- Modify: `.gitignore`
- Create: `.github/workflows/native-ocr-experiment.yml`
- Test: `native/ocr_preprocessing/tests/abi_contract.cpp`

- [ ] **Step 1: Add the C header with the exact Python layout**

Define `AGETHA_OCR_ABI_VERSION 1`, fixed-width enums/structs, `extern "C"`, and these exports:

```cpp
AGETHA_OCR_API std::uint32_t agetha_ocr_abi_version() noexcept;
AGETHA_OCR_API std::uint32_t agetha_ocr_request_size_v1() noexcept;
AGETHA_OCR_API std::uint32_t agetha_ocr_result_size_v1() noexcept;
AGETHA_OCR_API std::int32_t agetha_ocr_preprocess_v1(
    const AgethaOcrRequestV1* request,
    std::uint8_t* output_gray,
    std::uint64_t output_capacity,
    AgethaOcrResultV1* result) noexcept;
```

Add `static_assert` checks for standard layout, field widths, and offsets. Do not export allocation/free functions.

- [ ] **Step 2: Add a native ABI-contract executable before algorithm code**

The C++ test must call every export and verify invalid/null requests return deterministic status codes without exceptions crossing the ABI. It must verify too-small output rejection and bytes-written behavior.

- [ ] **Step 3: Configure a minimal CMake project**

Build one shared library and one native test executable with C++17, warnings enabled, no external package manager, and Windows system libraries only:

```cmake
target_link_libraries(agetha_ocr_preprocessing PRIVATE windowscodecs ole32)
target_compile_definitions(agetha_ocr_preprocessing PRIVATE AGETHA_OCR_BUILD_DLL)
add_test(NAME native_ocr_abi_contract COMMAND native_ocr_abi_contract)
```

- [ ] **Step 4: Add build-output ignores**

Ignore only scoped outputs such as `/native/ocr_preprocessing/build/` and `/benchmark-results/`. Do not broaden ignores over source, fixtures, or checked-in reports.

- [ ] **Step 5: Add the dedicated CI matrix**

Use explicit architecture entries:

```yaml
strategy:
  fail-fast: false
  matrix:
    include:
      - runner: windows-latest
        architecture: x64
        cmake_arch: x64
      - runner: windows-11-vs2026-arm
        architecture: arm64
        cmake_arch: ARM64
```

For each entry: checkout, set up Python 3.13 with the matching architecture, install pinned test dependencies, configure/build CMake, run `ctest`, run Python ABI/contract tests against the built DLL, run parity tests, run benchmarks, and upload DLL plus JSON/Markdown evidence as artifacts. Add an Ubuntu job that imports the loader and proves deterministic Python fallback.

The workflow may run on pull requests and manual dispatch. Before the first PR exists, a branch push requires separate user authorization; do not treat a workflow file as permission to publish.

- [ ] **Step 6: Validate what is possible locally**

Run CMake discovery without installing anything:

```powershell
cmake --version
cl
```

Expected on the current host: toolchain unavailable. Record this as an environment limitation, not a product failure. Run YAML/static checks and Python layout tests locally.

---

## Task 4: Implement the Bounded WIC Preprocessing Primitive

**Files:**

- Modify: `native/ocr_preprocessing/src/agetha_ocr_preprocessing.cpp`
- Modify: `native/ocr_preprocessing/tests/abi_contract.cpp`
- Modify: `tests/test_native_ocr_preprocessing.py`
- Test: `native/ocr_preprocessing/tests/abi_contract.cpp`
- Test: `tests/test_native_ocr_preprocessing.py`

- [ ] **Step 1: Write native-call tests for known pixels and dimensions**

Add cases for 1x1, odd dimensions, RGB stride, downscale-plus-upscale, basic grayscale, auto bright, auto dark/invert, invalid mode, overflow dimensions, and output-capacity mismatch. The Python test must compare the real DLL when `AGETHA_NATIVE_OCR_DLL` points to a built artifact; otherwise it records an explicit skip limited to real-DLL cases.

- [ ] **Step 2: Implement COM/WIC initialization with bounded ownership**

Inside each call, use `CoInitializeEx(nullptr, COINIT_MULTITHREADED)`, accept `RPC_E_CHANGED_MODE` without changing the caller's apartment, and call `CoUninitialize()` only when this function successfully initialized COM. Use RAII `ComPtr` ownership for factories, bitmaps, scalers, and format converters. No worker thread, global pixel cache, callback, window handle, or screen API belongs in this DLL.

- [ ] **Step 3: Implement exact dimension validation and WIC conversion**

Validate all multiplication/addition before allocating or indexing. Wrap the caller RGB buffer as a WIC bitmap, apply the provided intermediate and final dimensions, convert to 8-bit grayscale, and copy exactly `output_width * output_height` bytes into the caller buffer.

Use WIC interpolation modes selected to approximate Pillow LANCZOS most closely. Record the chosen mode in the benchmark report; do not claim exact pixel identity.

- [ ] **Step 4: Implement bounded `auto` postprocessing**

Operate only on the caller output buffer:

1. Compute histogram/min/max and mean.
2. Apply the same no-cutoff autocontrast mapping as Pillow.
3. Invert when the post-autocontrast mean is below `70.0`.
4. Apply the Pillow `SHARPEN` 3x3 kernel semantics with clamped edge behavior.

No native allocation may scale above the validated output byte count plus bounded row/histogram scratch storage. Catch all exceptions at the export boundary and return `INTERNAL_ERROR`.

- [ ] **Step 5: Run local Python tests and require CI for real-DLL green status**

Local command:

```powershell
python -m unittest tests.test_native_ocr_preprocessing -v
```

Do not claim the C++ implementation passes until both AMD64 and ARM64 CI execute the real library tests.

---

## Task 5: Run Both Backends Through One Parity and OCR-Quality Suite

**Files:**

- Modify: `tests/ocr_preprocessing_contract.py`
- Create: `tests/test_native_ocr_preprocessing_parity.py`
- Create: `tests/fixtures/ocr_preprocessing/README.md`
- Test: `tests/test_native_ocr_preprocessing_parity.py`

- [ ] **Step 1: Add real-DLL backend discovery with explicit skip semantics**

The test reads only `AGETHA_NATIVE_OCR_DLL`. If absent, real-native tests skip with `native OCR DLL not supplied`; the dedicated Windows CI must set it and include a guard test that fails if no native test ran.

- [ ] **Step 2: Apply the same contract to Python and native**

Run `assert_backend_contract()` once with `preprocess_ocr_image` and once with `NativeOCRPreprocessor.preprocess`. This is the authoritative cross-backend contract, not duplicate native-only assertions.

- [ ] **Step 3: Add pixel-parity comparisons**

For every fixture, mode, max dimension, and upscale combination, compare output dimensions/scales exactly and measure mean/p99/max pixel errors. Enforce the predetermined basic/auto thresholds from Task 1.

- [ ] **Step 4: Add OCR text, confidence, and coordinate parity**

Generate non-sensitive text fixtures at test time using an available deterministic TrueType font, with the selected font name recorded in test output. Run the same local Tesseract executable and configuration against Python and native results.

Normalize tokens with casefolding and whitespace/punctuation normalization. Match duplicate tokens in reading order. Convert both word boxes back to source coordinates using their respective `scale_x`/`scale_y`. Enforce:

- token recall >= 0.98 relative to the Python result;
- median confidence delta <= 5 percentage points;
- at least 95% of matched boxes have IoU >= 0.85 and center delta <= 3 source pixels.

The dedicated CI installs/configures Tesseract and fails if OCR parity did not execute. Ordinary local runs may skip only when the executable is genuinely unavailable.

- [ ] **Step 5: Run focused parity tests**

Run locally without a DLL to verify reference and skip behavior:

```powershell
python -m unittest tests.test_ocr_preprocessing_contract tests.test_native_ocr_preprocessing_parity -v
```

Run in Windows CI with `AGETHA_NATIVE_OCR_DLL` set and verify zero native skips.

---

## Task 6: Build the End-to-End Benchmark and Qualification Evaluator

**Files:**

- Create: `benchmarks/__init__.py`
- Create: `benchmarks/ocr_preprocessing.py`
- Create: `benchmarks/evaluate_native_ocr.py`
- Create: `tests/test_native_ocr_benchmark.py`
- Create: `docs/benchmarks/issue-38-native-ocr-preprocessing.md`
- Modify: `.github/workflows/native-ocr-experiment.yml`
- Test: `tests/test_native_ocr_benchmark.py`

- [ ] **Step 1: Write failing statistics and qualification tests**

Test percentile calculation, warmup exclusion, per-cell grouping, architecture normalization, 20% median gate, p95/p99 guard, parity failure rejection, CPU/memory regression rejection, malformed/missing evidence rejection, and deterministic JSON/Markdown rendering.

Use this result model:

```python
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
    parity_passed: bool


@dataclass(frozen=True)
class QualificationDecision:
    architecture: str
    mode: str
    size: str
    qualified: bool
    median_improvement: float
    reasons: tuple[str, ...]
```

- [ ] **Step 2: Run the evaluator tests and verify RED**

Run:

```powershell
python -m unittest tests.test_native_ocr_benchmark -v
```

Expected: import failure because the benchmark modules do not exist.

- [ ] **Step 3: Implement a complete-boundary benchmark**

The timed native callable must include:

- RGB normalization;
- `tobytes()`;
- input/output allocation;
- `ctypes` invocation;
- all WIC/native work;
- PIL reconstruction;
- access to at least one output byte so lazy work cannot escape timing.

The Python callable times the existing `preprocess_ocr_image()` from entry through returned PIL-image access. Do not time only the C++ function.

Benchmark 640x360, 1280x720, 1920x1080, and 3840x2160 for `basic` and `auto`, with deterministic input families. Measure DLL load plus first call separately, then at least 10 warmups and enough alternating randomized backend calls to obtain 50 valid samples per cell. Store raw samples as well as median/p95/p99.

- [ ] **Step 4: Measure resource and repeated-call behavior**

Record process CPU time, peak working set, retained memory after forced collection, DLL load time, first-call time, and a 1,000-call reliability loop at a representative size. Reject crashes, output drift, monotonically retained memory, or materially worse CPU/memory even if latency passes.

Use `psutil` where available and Windows process APIs only behind a benchmark helper. Benchmark instrumentation is not imported by Agetha runtime.

- [ ] **Step 5: Implement the per-cell evaluator**

A cell qualifies only when:

```python
median_improvement = 1.0 - native.median_ms / python.median_ms
median_improvement >= 0.20
native.p95_ms <= python.p95_ms * 1.10
native.p99_ms <= python.p99_ms * 1.10
parity_passed is True
```

Also require the predetermined global resource/reliability/cold-start gates: CPU <= 110% of Python, incremental peak working set <= Python plus 32 MiB, 1,000-call retained growth <= Python plus 8 MiB, zero failures/output drift, and load-plus-first-call <= Python plus 250 ms. Report negative outcomes as `qualified: false` with reasons; benchmark completion itself remains a successful CI outcome.

- [ ] **Step 6: Add machine and human reports**

CI uploads raw JSON and rendered Markdown. The checked-in report records runner image, architecture, toolchain, Python/Pillow/Tesseract versions, commit SHA, iteration count, cold/warm results, parity summary, resource results, and the resulting keep/reject decision. Generated benchmark evidence is never loaded by runtime.

- [ ] **Step 7: Run focused tests and a Python-only benchmark smoke**

Run:

```powershell
python -m unittest tests.test_native_ocr_benchmark -v
python -m benchmarks.ocr_preprocessing --python-only --iterations 3 --output benchmark-results/python-smoke.json
```

Delete the scratch output after verifying its schema.

---

## Task 7: GitHub CI Evidence Checkpoint

**Files:**

- Inspect: all files from Tasks 1-6
- Output artifact: AMD64 benchmark JSON/Markdown/DLL
- Output artifact: ARM64 benchmark JSON/Markdown/DLL

- [ ] **Step 1: Run the complete local pre-publication validation**

Run with the project venv and a workspace-owned temp root where needed:

```powershell
python -m unittest tests.test_ocr_preprocessing_contract tests.test_native_ocr_preprocessing tests.test_native_ocr_preprocessing_parity tests.test_native_ocr_benchmark -v
python -m unittest discover -s tests -v
python -m compileall agetha benchmarks tests
python -m agetha.commands.generate_command_matrix --check
python -m agetha.config.generate_settings_reference --check
git diff --check
git status --short
```

Classify the already-established Codex Windows `TemporaryDirectory`/symlink limitation separately; do not weaken tests or ACLs.

- [ ] **Step 2: Inspect exact publication scope and obtain explicit approval**

Show the user the proposed commits, branch, workflow triggers, and paths. Do not commit/push solely because implementation was authorized.

- [ ] **Step 3: After approval, create explicit scoped commits and push only this branch**

Stage named paths only. Never use `git add .`, `git add -A`, or `git add --all`.

- [ ] **Step 4: Wait for both architecture jobs and inspect artifacts**

Require:

- native build and `ctest` green on AMD64;
- native build and `ctest` green on ARM64;
- Python loader/contract/parity tests green with zero native skips on both;
- Ubuntu import/fallback green;
- complete benchmark JSON and Markdown present for both architectures.

If either architecture cannot compile/load, fix only the experiment boundary and rerun. Do not mark the unavailable architecture qualified.

- [ ] **Step 5: Reproduce or rerun suspicious measurements**

Treat a single noisy CI run as insufficient. Rerun each potentially qualifying architecture at least twice and require the median gate to hold across runs. Record all run URLs/IDs in the evidence document.

---

## Task 8A: Retain Only Qualified Runtime Cells

Execute this task only if at least one architecture/mode/size cell passes Task 7.

**Files:**

- Modify: `agetha/platform/native_ocr_preprocessing.py`
- Modify: `agetha/platform/screen_reader.py`
- Modify: `agetha/app_config.py`
- Modify: `agetha/config/schema.py`
- Modify: `agetha/ui/dashboard.py`
- Modify: `config.txt`
- Modify: `docs/generated/settings_reference.md`
- Modify: `docs/development.md`
- Modify: `docs/module_reference.md`
- Modify: `docs/runtime_flows.md`
- Modify: `docs/benchmarks/issue-38-native-ocr-preprocessing.md`
- Create: `tests/test_native_ocr_preprocessing_integration.py`
- Modify: `tests/test_setting_specs.py`
- Test: `tests/test_native_ocr_preprocessing_integration.py`

- [ ] **Step 1: Write failing selector/fallback integration tests**

Cover:

- default `auto` uses native only for an explicitly qualified architecture/mode/size cell;
- unqualified cells always use Pillow;
- `python` always uses Pillow;
- explicit `native` still falls back when the current cell is unqualified or unavailable;
- missing/wrong ABI/call failure uses Pillow exactly once;
- ScreenReader receives an equivalent `ProcessedOCRImage` and Tesseract call/coordinate mapping is unchanged;
- native failure does not duplicate OCR, history, memory, capture, or capability work;
- Compact/capture authorization ordering is untouched because preprocessing runs only after an approved frame exists;
- no native load occurs at application import/startup unless OCR preprocessing is invoked.

- [ ] **Step 2: Add a static, source-owned qualification table**

Encode only reviewed CI decisions, not mutable benchmark output:

```python
QUALIFIED_CELLS: frozenset[tuple[str, str, str]] = frozenset({
    # Populated only with architecture/mode/size cells proven by checked evidence.
})
```

The runtime never reads Markdown/JSON benchmark artifacts. Each entry must cite the evidence commit/run in an adjacent source comment or architecture document. If a future build changes the algorithm/toolchain materially, qualification must be rerun.

- [ ] **Step 3: Implement the safe selector around the existing reference**

Add a small callable selected in `ScreenReader.__init__` and replace only the call at the current `preprocess_ocr_image()` site. The wrapper catches `NativePreprocessError`, records a non-sensitive diagnostic reason, and immediately invokes the original function with the identical image/parameters.

Do not move screen capture, `_capture_lock`, OCR analysis, target metadata, or capability checks.

- [ ] **Step 4: Add conservative configuration metadata**

Only because at least one cell qualified, add:

```text
OCR_PREPROCESSING_BACKEND = auto
```

with enum choices `auto`, `python`, `native`, restart required. Add one `SettingSpec`, one `AppSettings` property, and matching Dashboard metadata. Preserve config discovery, comments, unknown keys, atomic writes, Compact persistence, and Fast Mode ownership.

- [ ] **Step 5: Update packaging instructions without committing binaries**

Document fixed DLL staging paths for `win-amd64` and `win-arm64`, SHA verification in release assembly, and frozen-bundle explicit binary inclusion. State that absence is supported and falls back. Do not claim frozen validation until a real packaged executable is built and smoke-tested.

- [ ] **Step 6: Regenerate/check settings docs and run focused tests**

Run:

```powershell
python -m agetha.config.generate_settings_reference
python -m unittest tests.test_native_ocr_preprocessing_integration tests.test_setting_specs tests.test_screen_monitoring_reliability -v
python -m agetha.config.generate_settings_reference --check
```

- [ ] **Step 7: Inspect authority and lifecycle boundaries**

Confirm the diff adds no command, capability, origin, CommandGuard, screen-target, exclusion, or privacy-policy change. Confirm every native error stays inside preprocessing fallback.

---

## Task 8B: Reject the Native Runtime Cleanly

Execute this task instead of Task 8A if no cell passes, or if parity/resource/reliability gates fail.

**Files:**

- Modify: `docs/benchmarks/issue-38-native-ocr-preprocessing.md`
- Remove from production scope: any runtime selector/config integration added experimentally
- Retain as evidence where useful: benchmark harness, native source, contract tests, CI experiment workflow

- [ ] **Step 1: Record the negative result precisely**

Document complete boundary latency for both architectures, each mode and size, cold/warm/resource/parity results, and the exact rejection reason. State that Pillow remains the production backend.

- [ ] **Step 2: Remove unqualified production surface**

Ensure there is no `OCR_PREPROCESSING_BACKEND` setting, no ScreenReader selector, no startup loader, no packaged DLL requirement, and no claim that native acceleration ships.

- [ ] **Step 3: Preserve reproducibility without creating dead runtime code**

Keep the benchmark/native experiment under development-only paths only if it remains executable and documented. If the loader is useful solely to run the benchmark, move it under `benchmarks/` rather than leaving a dead `agetha.platform` API.

- [ ] **Step 4: Run the unchanged-production regressions**

Run:

```powershell
python -m unittest tests.test_screen_monitoring_reliability tests.test_screen_reader_advanced tests.test_setting_specs -v
python -m agetha.config.generate_settings_reference --check
```

Expected: production behavior and settings remain identical to the pre-experiment branch.

---

## Task 9: Final Cross-Platform and Security Verification

**Files:**

- Inspect: complete branch diff
- Update: `docs/benchmarks/issue-38-native-ocr-preprocessing.md`
- Update: this plan's checkboxes as tasks complete

- [ ] **Step 1: Run focused preprocessing suites**

Run all reference, loader, ABI, parity, integration/unchanged-production, and benchmark tests. On native CI, assert no real-native test was skipped.

- [ ] **Step 2: Run architecture/security regressions**

Run the current capability-generation, screen-reader authorization, capture privacy/exclusion, Compact transition, command authority, and Computer Use target-policy suites. The exact suite names should be selected with `rg --files tests` and recorded in the final report.

- [ ] **Step 3: Run full validation**

```powershell
python -m unittest discover -s tests -v
python -m compileall agetha benchmarks tests
python -m agetha.commands.generate_command_matrix --check
python -m agetha.config.generate_settings_reference --check
git diff --check
git status --short
```

Use the project venv. If the Codex sandbox reproduces the known Windows protected-temp or symlink restriction, preserve the tests and use the already-approved external-validation procedure.

- [ ] **Step 4: Audit artifacts and imports**

Confirm:

- no DLL/PDB/OBJ/build tree is tracked;
- no benchmark scratch image/result is untracked;
- no absolute developer path is embedded in source/docs;
- Linux imports and Python fallback pass;
- no native library loads during `import agetha.platform.screen_reader`;
- retained settings/generated docs agree;
- runtime never consumes generated benchmark documentation;
- the parent dirty worktree was untouched.

- [ ] **Step 5: Review the complete diff against the decision**

If retained, every production native cell must be supported by repeated CI evidence. If rejected, production must remain Pillow-only. Remove comments, abstractions, and compatibility surfaces that exist only for an outcome that was not selected.

- [ ] **Step 6: Produce the final evidence report before requesting Git publication**

Report:

- exact base and branch SHA;
- AMD64 and ARM64 build/load results;
- per-mode/per-size median, p95, p99, CPU, memory, cold-start, and reliability results;
- pixel and OCR parity results;
- qualified cells or rejection decision;
- fallback/security review;
- local, external, and CI validation separately;
- exact Git diff/status;
- explicit commit/push/PR scope for user approval.

Do not push, create a PR, merge, or publish a release without a new explicit authorization.

---

## Expected Decision Outcomes

### Retain

Retain the optional backend only when at least one cell passes all gates. Production remains Python by default for every unqualified or unavailable cell, and the native DLL stays optional.

### Reject

Reject production integration when no cell clears the full-boundary gate or when correctness/resource/reliability regresses. Keep the Pillow implementation unchanged and preserve enough experiment code/evidence to make the result reproducible without burdening runtime maintenance.
