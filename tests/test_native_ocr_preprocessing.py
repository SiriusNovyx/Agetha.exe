from __future__ import annotations

import ctypes
import gc
import importlib
import platform
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

class _FakeExport:
    def __init__(self, implementation):
        self._implementation = implementation
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self._implementation(*args)


class _FakeNativeLibrary:
    def __init__(
        self,
        module,
        *,
        abi_version: int | None = None,
        request_size: int | None = None,
        result_size: int | None = None,
        call_status: int = 0,
        reported_status: int = 0,
        bytes_written_delta: int = 0,
    ):
        self._module = module
        self.call_status = call_status
        self.reported_status = reported_status
        self.bytes_written_delta = bytes_written_delta
        self.agetha_ocr_abi_version = _FakeExport(
            lambda: module.ABI_VERSION if abi_version is None else abi_version,
        )
        self.agetha_ocr_request_size_v1 = _FakeExport(
            lambda: ctypes.sizeof(module.NativeRequestV1)
            if request_size is None else request_size,
        )
        self.agetha_ocr_result_size_v1 = _FakeExport(
            lambda: ctypes.sizeof(module.NativeResultV1)
            if result_size is None else result_size,
        )
        self.agetha_ocr_preprocess_v1 = _FakeExport(self._preprocess)

    def _preprocess(self, request_pointer, output_pointer, output_capacity,
                    result_pointer):
        request = ctypes.cast(
            request_pointer, ctypes.POINTER(self._module.NativeRequestV1),
        ).contents
        result = ctypes.cast(
            result_pointer, ctypes.POINTER(self._module.NativeResultV1),
        ).contents
        capacity = int(getattr(output_capacity, "value", output_capacity))
        output = ctypes.cast(output_pointer, ctypes.POINTER(ctypes.c_uint8))
        for index in range(capacity):
            output[index] = (index * 7 + request.mode * 13) % 256
        result.status = self.reported_status
        result.bytes_written = capacity + self.bytes_written_delta
        return self.call_status


class NativeOCRPreprocessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module(
            "agetha.platform.native_ocr_preprocessing",
        )

    def _processor(self, **library_options):
        library = _FakeNativeLibrary(self.module, **library_options)
        processor = self.module.NativeOCRPreprocessor._from_loaded_library_for_tests(
            library,
            Path("C:/test/agetha_ocr_preprocessing.dll"),
        )
        return processor, library

    def test_import_does_not_load_a_library(self):
        loader_name = "WinDLL" if hasattr(ctypes, "WinDLL") else "CDLL"
        with patch.object(ctypes, loader_name) as loader:
            importlib.reload(self.module)
        loader.assert_not_called()

    def test_default_path_is_absolute_and_package_controlled(self):
        base = Path("C:/Agetha/agetha")

        resolved = self.module.default_native_library_path(
            package_root=base,
            system="Windows",
            machine="AMD64",
        )

        self.assertEqual(
            resolved,
            base / "native" / "win-amd64" / "agetha_ocr_preprocessing.dll",
        )
        self.assertTrue(resolved.is_absolute())

    def test_unsupported_platform_is_unavailable_without_loading(self):
        with patch.object(self.module.NativeOCRPreprocessor, "from_library") as load:
            processor, availability = self.module.load_default_native_preprocessor(
                system="Linux",
                machine="x86_64",
            )

        self.assertIsNone(processor)
        self.assertFalse(availability.available)
        self.assertEqual(availability.reason, "unsupported_platform")
        load.assert_not_called()

    def test_missing_default_library_is_unavailable(self):
        processor, availability = self.module.load_default_native_preprocessor(
            package_root=Path("C:/definitely-missing-agetha-package"),
            system="Windows",
            machine="AMD64",
        )

        self.assertIsNone(processor)
        self.assertFalse(availability.available)
        self.assertEqual(availability.reason, "library_missing")

    def test_default_loader_rejects_library_outside_package_root(self):
        escaped = Path("C:/outside/agetha_ocr_preprocessing.dll")
        with (
            patch.object(self.module, "default_native_library_path",
                         return_value=escaped),
            patch.object(Path, "is_file", return_value=True),
            patch.object(self.module.NativeOCRPreprocessor, "from_library") as load,
        ):
            processor, availability = self.module.load_default_native_preprocessor(
                package_root=Path("C:/trusted/agetha"),
                system="Windows",
                machine="AMD64",
            )

        self.assertIsNone(processor)
        self.assertEqual(availability.reason, "library_outside_package")
        load.assert_not_called()

    def test_abi_version_mismatch_is_rejected(self):
        library = _FakeNativeLibrary(self.module, abi_version=99)

        with self.assertRaisesRegex(
            self.module.NativePreprocessError, "ABI_MISMATCH",
        ):
            self.module.NativeOCRPreprocessor._from_loaded_library_for_tests(
                library, Path("C:/test/wrong-version.dll"),
            )

        self.assertEqual(len(library.agetha_ocr_abi_version.calls), 1)

    def test_request_structure_size_mismatch_is_rejected(self):
        library = _FakeNativeLibrary(self.module, request_size=1)

        with self.assertRaisesRegex(
            self.module.NativePreprocessError, "ABI_MISMATCH",
        ):
            self.module.NativeOCRPreprocessor._from_loaded_library_for_tests(
                library, Path("C:/test/wrong-request-size.dll"),
            )

    def test_result_structure_size_mismatch_is_rejected(self):
        library = _FakeNativeLibrary(self.module, result_size=1)

        with self.assertRaisesRegex(
            self.module.NativePreprocessError, "ABI_MISMATCH",
        ):
            self.module.NativeOCRPreprocessor._from_loaded_library_for_tests(
                library, Path("C:/test/wrong-result-size.dll"),
            )

    def test_success_uses_rgb_input_and_caller_owned_grayscale_output(self):
        processor, library = self._processor()
        image = Image.new("RGBA", (3, 2), (10, 20, 30, 40))

        processed = processor.preprocess(
            image, max_dimension=10, mode="basic", upscale=2,
        )

        self.assertEqual(processed.image.mode, "L")
        self.assertEqual(processed.image.size, (6, 4))
        self.assertEqual(processed.image.getpixel((1, 0)), 7)
        self.assertEqual(processed.scale_x, 2.0)
        self.assertEqual(processed.scale_y, 2.0)
        self.assertEqual(len(library.agetha_ocr_preprocess_v1.calls), 1)
        request = ctypes.cast(
            library.agetha_ocr_preprocess_v1.calls[0][0],
            ctypes.POINTER(self.module.NativeRequestV1),
        ).contents
        self.assertEqual(request.input_stride, 9)
        self.assertEqual(request.input_length, 18)

    def test_output_buffer_lifetime_survives_processor_locals(self):
        processor, _library = self._processor()
        processed = processor.preprocess(
            Image.new("RGB", (10, 10), "white"),
            max_dimension=10,
            mode="auto",
            upscale=2,
        )

        gc.collect()

        self.assertEqual(processed.image.getpixel((19, 19)),
                         (399 * 7 + 13) % 256)

    def test_native_return_status_is_reported_without_pixel_contents(self):
        processor, _library = self._processor(call_status=5)
        secret = "private-screen-secret"

        with self.assertRaises(self.module.NativePreprocessError) as raised:
            processor.preprocess(
                Image.new("RGB", (20, 10), "white"),
                max_dimension=20,
                mode="basic",
                upscale=2,
            )

        self.assertEqual(raised.exception.status,
                         self.module.NativePreprocessStatus.WIC_FAILURE)
        self.assertNotIn(secret, str(raised.exception))

    def test_result_status_is_reported(self):
        processor, _library = self._processor(reported_status=4)

        with self.assertRaises(self.module.NativePreprocessError) as raised:
            processor.preprocess(
                Image.new("RGB", (20, 10), "white"),
                max_dimension=20,
            )

        self.assertEqual(
            raised.exception.status,
            self.module.NativePreprocessStatus.COM_INITIALIZATION_FAILED,
        )

    def test_short_write_is_rejected(self):
        processor, _library = self._processor(bytes_written_delta=-1)

        with self.assertRaisesRegex(
            self.module.NativePreprocessError, "OUTPUT_TOO_SMALL",
        ):
            processor.preprocess(
                Image.new("RGB", (20, 10), "white"),
                max_dimension=20,
            )

if __name__ == "__main__":
    unittest.main()
