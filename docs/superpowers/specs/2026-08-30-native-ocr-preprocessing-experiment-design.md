# Native OCR Preprocessing Experiment Design

Date: 2026-08-30  
Issue: [#38](https://github.com/SiriusNovyx/Agetha.exe/issues/38)  
Baseline: `SiriusNovyx/Agetha.exe:main` at `5260ad28ecba0eb754b3db26d182c367ea39bb86`

## Purpose

This slice answers one question:

> Does an optional native OCR preprocessing backend provide enough real
> end-to-end benefit to justify its build and maintenance cost?

It is an experiment with a production-quality rejection path, not a commitment
to migrate more of Agetha to C++. The existing Pillow implementation remains
the reference and mandatory fallback. A native backend is retained and made
eligible only where measured end-to-end results clear the decision gate without
material correctness, reliability, CPU, or memory regressions.

## Current evidence

The current Python function delegates its expensive image work to Pillow's
native implementation. Synthetic 1920x1080 measurements on the development
machine produced these preliminary figures:

| Operation | Preliminary best time |
|---|---:|
| Complete Pillow `basic` preprocessing | 117 ms |
| Complete Pillow `auto` preprocessing | 215 ms |
| `RGB Image.tobytes()` input normalization | 4.99 ms |
| 3840x2160 output-buffer allocation | 2.27 ms |
| `Image.frombytes()` output reconstruction | 2.71 ms |
| Safe `Image.frombuffer()` output view | 0.011 ms |

These are orientation measurements, not release evidence. Final measurements
come from the committed benchmark harness on native Windows AMD64 and ARM64 CI
runners and include the complete Python-to-native-to-Python path.

At 1920x1080, a 20% latency improvement means no more than 93.6 ms for
`basic` and 172 ms for `auto`. The measured input/output boundary consumes
roughly 7-10 ms before DLL and WIC work. The experiment therefore does not
assume that WIC will beat Pillow.

## Scope

This slice may add:

- one optional Windows C++17 OCR preprocessing library;
- one versioned C ABI with caller-owned buffers;
- one lazy, absolute-path Python loader;
- backend selection local to `ScreenReader` preprocessing;
- shared backend-contract and parity tests;
- deterministic OCR-quality fixtures;
- an end-to-end benchmark harness and machine-readable results;
- native AMD64 and ARM64 GitHub Actions jobs;
- documentation for build, fallback, qualification, and packaging.

This slice does not move or change:

- capture target selection or screen/window capture;
- OCR policy, PSM selection, language selection, or Tesseract invocation;
- privacy exclusions, redaction, or external-context policy;
- Compact/Full capability policy, generations, or effect authorization;
- Command Guard, command dispatch, Computer Use, or input primitives;
- AI prompts, continuation, history, memory, providers, or personality;
- Tk ownership, worker scheduling, or application startup requirements;
- DXGI, Win32 window integration, audio, or other proposed native candidates.

## Approaches considered

### Chosen: versioned C ABI loaded with `ctypes`

The native component is a Windows DLL with a small C ABI. Python owns input and
output storage and retains all policy. This avoids a Python-version-specific
extension ABI, cross-runtime allocation rules, and a new runtime package.

### Rejected for this slice: pybind11 extension

Pybind11 would improve type ergonomics but introduces a build dependency and a
Python-extension packaging surface that does not help answer the performance
question. It can be reconsidered only if later native work needs richer object
interchange.

### Rejected for this slice: helper process

A helper process improves crash isolation but adds process lifetime, IPC,
serialization, and additional copies. Those costs are disproportionate for one
local image transform and would obscure the first experiment.

### Rejected for this slice: custom C++ Lanczos implementation

Maintaining a bespoke resampler would add more risk than this experiment can
justify. The candidate uses Windows Imaging Component for scaling/conversion
and small bounded native loops for the `auto` postprocessing steps. Quality
tests decide whether that output is sufficiently equivalent to Pillow.

## Ownership and runtime flow

Python remains authoritative:

```text
already-approved CapturedFrame.image
        |
        v
Python backend selector
        |
        +-- Python/Pillow reference
        |
        +-- qualified native backend
                |
                v
        normalize RGB bytes
                |
                v
        versioned C ABI call
                |
                v
        validated caller-owned Gray8 buffer
                |
                v
        PIL Image.frombuffer view
        (buffer lifetime retained by result owner)
        |
        v
existing Tesseract backend and coordinate transform
```

The native library receives image bytes only after existing Python capture,
target, mapping, exclusion, and capability checks have completed. It has no API
for choosing or capturing a target. It has no file, network, input, process,
provider, UI, or callback facility.

`ScreenReader` selects a preprocessing callable during construction. The
existing `preprocess_ocr_image()` remains the direct Python reference and a
compatibility surface. Native load or invocation failure is caught at the
backend boundary, recorded as a bounded diagnostic reason, and reruns the same
operation through Pillow. Failure never aborts startup or returns a partial
native image.

## C ABI and memory contract

The ABI is versioned and uses fixed-width integer fields. Conceptually it
contains:

```c
uint32_t agetha_ocr_abi_version(void);

int32_t agetha_ocr_preprocess_v1(
    const AgethaOcrRequestV1* request,
    uint8_t* output,
    uint64_t output_capacity,
    AgethaOcrResultV1* result
);
```

The request contains:

- ABI and structure sizes;
- an RGB24 input pointer, byte length, width, height, and stride;
- Python-computed intermediate and final dimensions;
- the bounded mode enum (`basic` or `auto`);
- reserved zeroed fields for compatible extension.

The result contains only status, bytes written, and reserved fields. Python
computes dimensions and scale factors using the existing reference rules so
rounding and coordinate ownership do not move into native code.

Memory rules:

- Python owns and pins the immutable input bytes for the duration of the call.
- Python allocates the mutable Gray8 output buffer.
- Native never retains a pointer after returning.
- Native never allocates memory that Python must free.
- Native validates all dimensions, strides, capacities, multiplication
  overflow, null pointers, modes, structure sizes, and output bounds.
- The exported call is `noexcept`; unexpected native failures become a status
  code rather than crossing the ABI.
- Python accepts output only after exact byte-count and metadata validation.
- `Image.frombuffer()` may be used only while the owning result retains the
  backing output buffer for at least the image lifetime. If that guarantee
  cannot be kept cleanly, the implementation uses the measured copying path.

## Native processing behavior

The candidate uses Windows Imaging Component for the same two logical resize
stages as the reference:

1. optionally reduce the longest edge to `max_dimension`;
2. upscale both dimensions by the existing factor;
3. convert to 8-bit grayscale.

`basic` ends after grayscale conversion. `auto` then performs bounded local
operations corresponding to the existing intent:

1. full-range autocontrast;
2. invert when mean luminance is below 70;
3. one fixed sharpen pass.

WIC interpolation is not assumed to be pixel-identical to Pillow Lanczos. The
candidate is acceptable only if deterministic image metrics and real Tesseract
output/coordinate tests meet the parity rules below.

COM initialization and WIC factory creation occur on the calling worker thread.
The implementation introduces no native worker or callback. Cold DLL loading
and first COM/WIC initialization are measured separately from repeated warm
calls. Any COM apartment or factory failure returns a bounded error and falls
back to Pillow.

## Backend selection and qualification

Qualification is architecture-, mode-, and size-aware. Passing one cell does
not grant eligibility to another.

Conceptually:

```text
(Windows architecture, preprocessing mode, input-size class)
        -> qualified native or Python reference
```

Examples:

- AMD64 `auto` may qualify while AMD64 `basic` stays on Pillow.
- AMD64 may qualify while ARM64 stays entirely on Pillow.
- 1080p/4K may qualify while small images stay on Pillow.

The production qualification table is source-owned mechanical evidence, not a
user authority setting. It cannot affect capture, command, privacy, or
capability policy. A user-facing backend preference is added only if at least
one cell qualifies. `auto` uses native only in qualified cells; `python` forces
the reference. An explicit `native` preference still falls back and cannot
override an unqualified architecture/mode/size cell.

If no cell qualifies, runtime native integration and the setting are removed
before final review. The benchmark report records the rejection.

## Contract and parity tests

The same backend contract runs against Pillow and the native candidate:

- output dimensions and `scale_x`/`scale_y`;
- RGB input modes and normalization;
- `basic` and `auto` behavior;
- bright, dark, low-contrast, high-contrast, and detailed images;
- small, 1080p, and 4K source dimensions;
- non-square scaling and coordinate round trips;
- empty, malformed, oversized, truncated, and overflow inputs;
- repeated calls and multiple Python worker threads;
- unavailable DLL, wrong architecture, wrong ABI, missing symbol, invalid
  result, and native error status;
- deterministic fallback to Pillow with no partial result publication;
- Linux import and forced-Python behavior without loading a Windows library.

Image parity is measured with documented mean/percentile/max error statistics;
it is not reduced to a single exact-byte assertion. Thresholds must be chosen
before examining the final benchmark result and must remain strict enough to
detect materially different contrast, inversion, or sharpening.

OCR parity uses deterministic local fixtures and the same Tesseract version and
configuration for both preprocessors. It records:

- normalized OCR text and token recall;
- word count and ordering;
- confidence distribution;
- bounding boxes transformed back to source/desktop coordinates;
- missed or newly invented words.

High-contrast fixtures require equivalent normalized text. More difficult
fixtures use a documented token-recall floor, bounded confidence change, and
coordinate tolerance. Any material loss, systematic coordinate drift, or
privacy-relevant recognition difference rejects that cell regardless of speed.

## Benchmark methodology

The benchmark covers small representative inputs, 1920x1080, and 3840x2160,
with `basic` and `auto` reported independently.

Every timed native iteration includes:

- PIL input normalization and `tobytes()`;
- output allocation;
- ctypes argument/view construction;
- DLL invocation;
- WIC/native processing;
- validated result handling;
- safe PIL reconstruction.

It reports:

- cold DLL load time;
- first-call COM/WIC initialization time;
- warm median, p95, and p99 latency after fixed warmup;
- process CPU time per call and CPU/wall ratio;
- peak RSS during the run;
- retained RSS after garbage collection and repeated calls;
- failures and fallback counts;
- Python/native speedup per architecture, mode, and size class.

At least 100 warm measured iterations are used for percentile results unless a
documented CI time limit forces a larger statistically justified batch method.
Benchmark inputs and iteration counts are identical between backends within one
runner. Results are written as JSON and a concise Markdown artifact. Generated
benchmark output is evidence and is never loaded by runtime policy.

## Decision gate

A cell qualifies only when all of these hold on its native architecture:

1. backend contract and fallback tests pass;
2. image and OCR parity requirements pass;
3. warm end-to-end median latency improves by at least 20%;
4. p95 and p99 show sustained benefit rather than tail regression;
5. CPU cost has no material regression;
6. peak and retained memory remain bounded with no repeated-call growth;
7. cold load/initialization remains acceptable and does not affect normal app
   startup because loading is lazy;
8. repeated and concurrent worker calls remain deterministic and crash-free.

A small-input cell may remain on Pillow even if larger cells qualify. An
architecture is never enabled merely because another architecture passed.

Failure to clear this gate is a successful negative result. The final branch
then removes unqualified runtime paths and documents why Pillow remains the
production backend.

## CI and build matrix

A dedicated native experiment workflow runs separately from ordinary Windows
and Linux unit CI:

| Runner | Python | Native build/test role |
|---|---|---|
| `windows-latest` | CPython 3.13 x64 | Build Release AMD64 DLL; run load, contract, parity, OCR, fallback, and benchmark tests |
| `windows-11-vs2026-arm` | CPython 3.13 ARM64 | Build Release ARM64 DLL; run the same native-architecture tests and benchmark |
| `ubuntu-latest` | CPython 3.13 x64 | Prove Windows-native modules import safely and always select Pillow |

CMake and the runner's Visual C++ toolchain are build-time requirements only.
No local compiler installation is required for ordinary development or Agetha
startup. Native binaries are CI artifacts and are not committed to Git.

The ordinary test workflow continues to run the full Python fallback on Windows
and Linux. Native jobs do not replace existing tests.

## Packaging and loading

The repository currently has no tracked, validated frozen-build specification,
so this slice does not claim that finding a DLL artifact proves a packaged
Agetha executable.

The loader searches only a fixed absolute project/bundle-relative path for the
current process architecture. It never searches the working directory or
`PATH`. CI stages its freshly built DLL at that path for tests.

If at least one cell qualifies, documentation records:

- exact CMake build commands for AMD64 and ARM64;
- expected artifact names and architecture paths;
- safe source-tree staging;
- the explicit PyInstaller `--add-binary`/bundle path required by a future
  frozen build;
- how to verify the loaded ABI and active/fallback backend.

Actual frozen packaging and GUI smoke results remain unperformed unless a
current executable is built and directly tested. Backend absence always leaves
the frozen application on Pillow.

## Documentation and result handling

The final change updates the platform/module/runtime documentation only for
behavior that survives the decision gate. A benchmark report records:

- source commit and workflow run;
- compiler, OS, CPU architecture, Python, Pillow, WIC/Windows, and Tesseract
  versions;
- fixture hashes and methodology;
- complete raw JSON artifact link or checked-in summarized results;
- qualification/rejection for each architecture, mode, and size class;
- limitations and unperformed manual/frozen validation.

Runtime never reads generated reports or documentation as configuration.

## Stop conditions

Stop the experiment and retain Pillow if:

- native input must broaden capture scope or move target/privacy policy;
- safe buffer ownership cannot be made obvious;
- the DLL can be loaded from an untrusted search path;
- native failure can crash or prevent startup;
- AMD64 or ARM64 cannot execute its own native contract tests;
- OCR quality or coordinate mapping regresses materially;
- performance gains disappear after boundary costs;
- the implementation requires a large binding/build framework or a custom
  image-processing subsystem disproportionate to the measured benefit.

