# Issue #38 native OCR preprocessing benchmark

Status: awaiting Windows AMD64 and ARM64 CI evidence.

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

## Evidence

The GitHub Actions run IDs, exact commit, runner images, toolchain versions, raw
JSON, repeated-run measurements, parity summary, and retain/reject decision will
be added only after the explicit publication checkpoint.
