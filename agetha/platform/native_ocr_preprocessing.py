"""Optional Windows-native OCR preprocessing boundary.

The module is safe to import on every supported platform and never loads a
native library at import time.  The existing Pillow implementation remains the
authoritative fallback owned by :mod:`agetha.platform.screen_monitoring`.
"""

from __future__ import annotations

import ctypes
import enum
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from agetha.platform.screen_monitoring import ProcessedOCRImage


ABI_VERSION = 1
_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
_SAFE_WINDOWS_LOAD_FLAGS = (
    _LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | _LOAD_LIBRARY_SEARCH_SYSTEM32
)


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
        super().__init__(
            f"native OCR preprocessing failed during {operation}: {status.name}",
        )
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


@dataclass(frozen=True)
class NativeBackendAvailability:
    available: bool
    architecture: str
    reason: str
    library_path: Path | None = None


def native_architecture_name(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> str | None:
    current_system = (system or platform.system()).strip().casefold()
    current_machine = (machine or platform.machine()).strip().casefold()
    if current_system != "windows":
        return None
    if current_machine in {"amd64", "x86_64"}:
        return "win-amd64"
    if current_machine in {"arm64", "aarch64"}:
        return "win-arm64"
    return None


def default_native_library_path(
    *,
    package_root: Path | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> Path | None:
    architecture = native_architecture_name(system=system, machine=machine)
    if architecture is None:
        return None
    root = (
        Path(package_root).resolve()
        if package_root is not None
        else Path(__file__).resolve().parents[1]
    )
    return root / "native" / architecture / "agetha_ocr_preprocessing.dll"


def _status_from_code(code: int) -> NativePreprocessStatus:
    try:
        return NativePreprocessStatus(int(code))
    except (TypeError, ValueError):
        return NativePreprocessStatus.INTERNAL_ERROR


def _path_is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


class NativeOCRPreprocessor:
    def __init__(self, library: Any, library_path: Path):
        self._library = library
        self.library_path = Path(library_path)

    @classmethod
    def from_library(cls, library_path: Path) -> "NativeOCRPreprocessor":
        resolved = Path(library_path).resolve(strict=True)
        if not resolved.is_file():
            raise NativePreprocessError(
                NativePreprocessStatus.INVALID_ARGUMENT,
                "library_path",
            )
        if platform.system() != "Windows" or not hasattr(ctypes, "WinDLL"):
            raise NativePreprocessError(
                NativePreprocessStatus.INVALID_ARGUMENT,
                "platform",
            )
        try:
            library = ctypes.WinDLL(
                str(resolved),
                winmode=_SAFE_WINDOWS_LOAD_FLAGS,
            )
        except (OSError, ValueError) as exc:
            raise NativePreprocessError(
                NativePreprocessStatus.INTERNAL_ERROR,
                "library_load",
            ) from exc
        return cls._from_loaded_library(library, resolved)

    @classmethod
    def _from_loaded_library_for_tests(
        cls,
        library: Any,
        library_path: Path,
    ) -> "NativeOCRPreprocessor":
        return cls._from_loaded_library(library, library_path)

    @classmethod
    def _from_loaded_library(
        cls,
        library: Any,
        library_path: Path,
    ) -> "NativeOCRPreprocessor":
        try:
            abi_version = library.agetha_ocr_abi_version
            request_size = library.agetha_ocr_request_size_v1
            result_size = library.agetha_ocr_result_size_v1
            preprocess = library.agetha_ocr_preprocess_v1
        except AttributeError as exc:
            raise NativePreprocessError(
                NativePreprocessStatus.ABI_MISMATCH,
                "exports",
            ) from exc

        abi_version.argtypes = []
        abi_version.restype = ctypes.c_uint32
        request_size.argtypes = []
        request_size.restype = ctypes.c_uint32
        result_size.argtypes = []
        result_size.restype = ctypes.c_uint32
        preprocess.argtypes = [
            ctypes.POINTER(NativeRequestV1),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint64,
            ctypes.POINTER(NativeResultV1),
        ]
        preprocess.restype = ctypes.c_int32

        if int(abi_version()) != ABI_VERSION:
            raise NativePreprocessError(
                NativePreprocessStatus.ABI_MISMATCH,
                "abi_version",
            )
        if int(request_size()) != ctypes.sizeof(NativeRequestV1):
            raise NativePreprocessError(
                NativePreprocessStatus.ABI_MISMATCH,
                "request_size",
            )
        if int(result_size()) != ctypes.sizeof(NativeResultV1):
            raise NativePreprocessError(
                NativePreprocessStatus.ABI_MISMATCH,
                "result_size",
            )
        return cls(library, Path(library_path))

    def preprocess(
        self,
        image: Image.Image,
        *,
        max_dimension: int,
        mode: str = "auto",
        upscale: int = 2,
    ) -> ProcessedOCRImage:
        original_width, original_height = image.size
        intermediate_width = original_width
        intermediate_height = original_height
        if max(original_width, original_height) > max_dimension:
            ratio = float(max_dimension) / max(original_width, original_height)
            intermediate_width = max(1, round(original_width * ratio))
            intermediate_height = max(1, round(original_height * ratio))
        output_width = max(1, intermediate_width * upscale)
        output_height = max(1, intermediate_height * upscale)

        rgb_bytes = image.convert("RGB").tobytes()
        input_buffer = (ctypes.c_uint8 * len(rgb_bytes)).from_buffer_copy(rgb_bytes)
        output_bytes = bytearray(output_width * output_height)
        output_buffer = (ctypes.c_uint8 * len(output_bytes)).from_buffer(output_bytes)
        request = NativeRequestV1(
            abi_version=ABI_VERSION,
            struct_size=ctypes.sizeof(NativeRequestV1),
            input_rgb=ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_uint8)),
            input_length=len(rgb_bytes),
            input_width=original_width,
            input_height=original_height,
            input_stride=original_width * 3,
            intermediate_width=intermediate_width,
            intermediate_height=intermediate_height,
            output_width=output_width,
            output_height=output_height,
            mode=1 if mode == "auto" else 0,
        )
        result = NativeResultV1(struct_size=ctypes.sizeof(NativeResultV1))

        try:
            call_status = int(self._library.agetha_ocr_preprocess_v1(
                ctypes.byref(request),
                output_buffer,
                ctypes.c_uint64(len(output_bytes)),
                ctypes.byref(result),
            ))
        except Exception as exc:
            raise NativePreprocessError(
                NativePreprocessStatus.INTERNAL_ERROR,
                "native_call",
            ) from exc
        if call_status != NativePreprocessStatus.OK:
            raise NativePreprocessError(
                _status_from_code(call_status),
                "native_call",
            )
        if result.status != NativePreprocessStatus.OK:
            raise NativePreprocessError(
                _status_from_code(result.status),
                "native_result",
            )
        if int(result.bytes_written) != len(output_bytes):
            raise NativePreprocessError(
                NativePreprocessStatus.OUTPUT_TOO_SMALL,
                "bytes_written",
            )

        processed = Image.frombuffer(
            "L",
            (output_width, output_height),
            output_bytes,
            "raw",
            "L",
            0,
            1,
        )
        return ProcessedOCRImage(
            image=processed,
            scale_x=output_width / max(1, original_width),
            scale_y=output_height / max(1, original_height),
        )


def load_default_native_preprocessor(
    *,
    package_root: Path | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> tuple[NativeOCRPreprocessor | None, NativeBackendAvailability]:
    architecture = native_architecture_name(system=system, machine=machine)
    if architecture is None:
        return None, NativeBackendAvailability(
            available=False,
            architecture="unsupported",
            reason="unsupported_platform",
        )
    library_path = default_native_library_path(
        package_root=package_root,
        system=system,
        machine=machine,
    )
    assert library_path is not None
    package_path = (
        Path(package_root).resolve()
        if package_root is not None
        else Path(__file__).resolve().parents[1]
    )
    if not _path_is_within(library_path, package_path):
        return None, NativeBackendAvailability(
            available=False,
            architecture=architecture,
            reason="library_outside_package",
            library_path=library_path,
        )
    if not library_path.is_file():
        return None, NativeBackendAvailability(
            available=False,
            architecture=architecture,
            reason="library_missing",
            library_path=library_path,
        )
    try:
        processor = NativeOCRPreprocessor.from_library(library_path)
    except NativePreprocessError as exc:
        return None, NativeBackendAvailability(
            available=False,
            architecture=architecture,
            reason=exc.operation,
            library_path=library_path,
        )
    return processor, NativeBackendAvailability(
        available=True,
        architecture=architecture,
        reason="available",
        library_path=library_path,
    )
