# Issue #38 native OCR preprocessing benchmark

Status: rejected after the mandatory pixel-parity gate.

This experiment compares the existing Pillow reference path with an optional
WIC-backed DLL. The native measurement includes RGB normalization, `tobytes()`,
caller-buffer allocation, `ctypes`, WIC processing, PIL reconstruction, and
output access. DLL load plus first call is reported separately from warm calls.

## Predetermined decision gate

A result is evaluated per architecture, mode, and input-size case. It must have:

- at least 20% warm median end-to-end latency improvement;
- native p95 and p99 no more than 10% slower than Pillow;
- passing pixel and OCR text/confidence/source-coordinate parity;
- CPU time no more than 10% above Pillow;
- incremental peak working set no more than 32 MiB above Pillow;
- retained memory no more than 8 MiB above Pillow after 1,000 repeated calls;
- DLL-load-plus-first-call no more than 250 ms above Pillow's first call;
- zero call failures or output drift.

The small size class requires both 640x360 and 1280x720 to pass. AMD64 and ARM64
are decided independently. No production backend is enabled from this document;
runtime never reads benchmark output.

## Decision

The optional native runtime candidate is rejected. Pillow remains Agetha's
production OCR preprocessing implementation on every architecture and for both
`basic` and `auto` modes. No runtime selector, setting, packaged DLL, or
`ScreenReader` integration was added.

The experiment stopped at its first mandatory failing gate. Broad pixel-parity
failures occurred on AMD64 and ARM64 before benchmarking, so all AMD64 and ARM64
cells for `basic` and `auto` at 640x360, 1280x720, 1920x1080, and 3840x2160 are
unqualified. There are deliberately no production qualification-table entries.

## GitHub Actions evidence

The initial and only run tested commit
`74ab03f6aaae0a16f2426007df10c0d9b6535f46`:

- [run 33284255399, attempt 1](https://github.com/SiriusNovyx/Agetha.exe/actions/runs/33284255399)
- [AMD64 job 99184568408](https://github.com/SiriusNovyx/Agetha.exe/actions/runs/33284255399/job/99184568408)
- [ARM64 job 99184568383](https://github.com/SiriusNovyx/Agetha.exe/actions/runs/33284255399/job/99184568383)
- [Linux fallback job 99184568270](https://github.com/SiriusNovyx/Agetha.exe/actions/runs/33284255399/job/99184568270)

Observed results:

- the AMD64 DLL configured and built successfully;
- the ARM64 DLL configured and built successfully;
- the native C++ ABI contract passed 1/1 on both architectures;
- real-DLL loading and the shared backend-contract test succeeded on both
  architectures;
- Linux lazy import and the Pillow-only runtime fallback passed;
- pixel parity failed on both Windows architectures;
- OCR text, confidence, and source-coordinate parity was not validated;
- the Windows jobs installed Tesseract successfully through Chocolatey, but the
  executable was not visible in the current workflow shell, causing two
  unexpected OCR-parity skips per architecture. Those skips are a workflow
  environment/configuration limitation, not passing evidence;
- the benchmark and artifact-upload steps were skipped after the parity suite
  failed.

The pixel failures reported by both jobs were:

| Fixture | Mode | AMD64 result | ARM64 result | Predetermined limit |
|---|---|---:|---:|---:|
| gradient | basic | mean error 12.709 | mean error 12.709 | mean error <= 4.0 |
| gradient | auto | mean error 14.724 | mean error 14.724 | mean error <= 6.0 |
| high contrast | auto | p99 error 41 | p99 error 65 | p99 error <= 32 |
| low contrast | auto | mean error 38.616 | mean error 38.616 | mean error <= 6.0 |
| dark | auto | mean error 40.166 | mean error 40.166 | mean error <= 6.0 |
| one-pixel axis | basic | mean error 17.789 | mean error 17.789 | mean error <= 4.0 |

## Intentionally unmeasured evidence

Median, p95, p99, CPU use, peak memory, retained memory, DLL/COM cold load,
first-call latency, and 1,000-call reliability were intentionally not measured.
The workflow's benchmark step follows parity and did not run after the mandatory
gate rejected the candidate. Running performance measurements after that result
would not change eligibility and would encourage tuning toward a predetermined
outcome.

No rerun was required because no cell was potentially qualifying. The retained
native source, ABI tests, parity contract, benchmark harness, and dedicated CI
workflow are reproducible Issue #38 evidence only. The explicit DLL wrapper is
kept under `benchmarks/`, not `agetha.platform`, and performs no import-time
library loading.
