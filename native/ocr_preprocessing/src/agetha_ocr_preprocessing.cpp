#include "agetha_ocr_preprocessing.h"

#include <Windows.h>
#include <wincodec.h>
#include <wrl/client.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <new>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace {

class ComInitialization final {
public:
    ComInitialization() noexcept : result_(CoInitializeEx(nullptr, COINIT_MULTITHREADED)) {}

    ~ComInitialization() {
        if (SUCCEEDED(result_)) {
            CoUninitialize();
        }
    }

    bool usable() const noexcept {
        return SUCCEEDED(result_) || result_ == RPC_E_CHANGED_MODE;
    }

private:
    HRESULT result_;
};

std::int32_t status_code(AgethaOcrStatus status) noexcept {
    return static_cast<std::int32_t>(status);
}

std::int32_t finish(
    AgethaOcrResultV1* result,
    AgethaOcrStatus status,
    std::uint64_t bytes_written = 0) noexcept {
    if (result != nullptr && result->struct_size == sizeof(AgethaOcrResultV1)) {
        result->status = status_code(status);
        result->bytes_written = bytes_written;
    }
    return status_code(status);
}

bool multiplication_fits(
    std::uint64_t left,
    std::uint64_t right,
    std::uint64_t* product) noexcept {
    if (left != 0 && right > std::numeric_limits<std::uint64_t>::max() / left) {
        return false;
    }
    *product = left * right;
    return true;
}

bool valid_request(
    const AgethaOcrRequestV1* request,
    std::uint8_t* output_gray,
    std::uint64_t output_capacity,
    AgethaOcrResultV1* result,
    std::uint64_t* required_output) noexcept {
    if (request == nullptr || result == nullptr || output_gray == nullptr) {
        return false;
    }
    if (result->struct_size != sizeof(AgethaOcrResultV1)) {
        return false;
    }
    if (request->abi_version != AGETHA_OCR_ABI_VERSION ||
        request->struct_size != sizeof(AgethaOcrRequestV1)) {
        return false;
    }
    if (request->input_rgb == nullptr || request->input_width == 0 ||
        request->input_height == 0 || request->intermediate_width == 0 ||
        request->intermediate_height == 0 || request->output_width == 0 ||
        request->output_height == 0) {
        return false;
    }
    if (request->mode > static_cast<std::uint32_t>(AgethaOcrMode::Auto)) {
        return false;
    }
    const auto minimum_stride = static_cast<std::uint64_t>(request->input_width) * 3U;
    if (request->input_stride < minimum_stride) {
        return false;
    }
    std::uint64_t minimum_input = 0;
    if (!multiplication_fits(
            request->input_stride, request->input_height, &minimum_input) ||
        request->input_length < minimum_input ||
        request->input_length > std::numeric_limits<UINT>::max()) {
        return false;
    }
    if (!multiplication_fits(
            request->output_width, request->output_height, required_output) ||
        *required_output > std::numeric_limits<UINT>::max()) {
        return false;
    }
    return output_capacity >= *required_output;
}

HRESULT scale_bitmap(
    IWICImagingFactory* factory,
    IWICBitmapSource* source,
    std::uint32_t width,
    std::uint32_t height,
    WICBitmapInterpolationMode interpolation,
    ComPtr<IWICBitmapScaler>* scaler) {
    HRESULT hr = factory->CreateBitmapScaler(scaler->ReleaseAndGetAddressOf());
    if (FAILED(hr)) {
        return hr;
    }
    return (*scaler)->Initialize(source, width, height, interpolation);
}

void apply_autocontrast(std::uint8_t* pixels, std::size_t count) {
    auto bounds = std::minmax_element(pixels, pixels + count);
    const int low = *bounds.first;
    const int high = *bounds.second;
    if (high <= low) {
        return;
    }
    const double scale = 255.0 / static_cast<double>(high - low);
    std::array<std::uint8_t, 256> lookup{};
    for (std::size_t index = 0; index < lookup.size(); ++index) {
        const int mapped = static_cast<int>((static_cast<int>(index) - low) * scale);
        lookup[index] = static_cast<std::uint8_t>(std::clamp(mapped, 0, 255));
    }
    for (std::size_t index = 0; index < count; ++index) {
        pixels[index] = lookup[pixels[index]];
    }
}

void apply_dark_inversion(std::uint8_t* pixels, std::size_t count) {
    std::uint64_t total = 0;
    for (std::size_t index = 0; index < count; ++index) {
        total += pixels[index];
    }
    if (static_cast<double>(total) / static_cast<double>(count) >= 70.0) {
        return;
    }
    for (std::size_t index = 0; index < count; ++index) {
        pixels[index] = static_cast<std::uint8_t>(255U - pixels[index]);
    }
}

void apply_sharpen(
    std::uint8_t* pixels,
    std::uint32_t width,
    std::uint32_t height) {
    if (width < 3 || height < 3) {
        return;
    }
    const std::size_t count = static_cast<std::size_t>(width) * height;
    std::vector<std::uint8_t> source(pixels, pixels + count);
    for (std::uint32_t y = 1; y + 1 < height; ++y) {
        for (std::uint32_t x = 1; x + 1 < width; ++x) {
            const auto center_index = static_cast<std::size_t>(y) * width + x;
            int neighbors = 0;
            for (int offset_y = -1; offset_y <= 1; ++offset_y) {
                for (int offset_x = -1; offset_x <= 1; ++offset_x) {
                    if (offset_x == 0 && offset_y == 0) {
                        continue;
                    }
                    const auto neighbor_y = static_cast<std::uint32_t>(
                        static_cast<int>(y) + offset_y);
                    const auto neighbor_x = static_cast<std::uint32_t>(
                        static_cast<int>(x) + offset_x);
                    neighbors += source[
                        static_cast<std::size_t>(neighbor_y) * width + neighbor_x];
                }
            }
            const int weighted = 32 * source[center_index] - 2 * neighbors;
            const int rounded = weighted >= 0 ? (weighted + 8) / 16 : (weighted - 8) / 16;
            pixels[center_index] = static_cast<std::uint8_t>(
                std::clamp(rounded, 0, 255));
        }
    }
}

AgethaOcrStatus preprocess_wic(
    const AgethaOcrRequestV1& request,
    std::uint8_t* output_gray,
    std::uint64_t required_output) {
    ComInitialization com;
    if (!com.usable()) {
        return AgethaOcrStatus::ComInitializationFailed;
    }

    ComPtr<IWICImagingFactory> factory;
    HRESULT hr = CoCreateInstance(
        CLSID_WICImagingFactory,
        nullptr,
        CLSCTX_INPROC_SERVER,
        IID_PPV_ARGS(factory.ReleaseAndGetAddressOf()));
    if (FAILED(hr)) {
        return AgethaOcrStatus::WicFailure;
    }

    ComPtr<IWICBitmap> input_bitmap;
    hr = factory->CreateBitmapFromMemory(
        request.input_width,
        request.input_height,
        GUID_WICPixelFormat24bppRGB,
        request.input_stride,
        static_cast<UINT>(request.input_length),
        const_cast<BYTE*>(request.input_rgb),
        input_bitmap.ReleaseAndGetAddressOf());
    if (FAILED(hr)) {
        return AgethaOcrStatus::WicFailure;
    }

    IWICBitmapSource* current_source = input_bitmap.Get();
    ComPtr<IWICBitmapScaler> downscale;
    if (request.intermediate_width != request.input_width ||
        request.intermediate_height != request.input_height) {
        hr = scale_bitmap(
            factory.Get(),
            current_source,
            request.intermediate_width,
            request.intermediate_height,
            WICBitmapInterpolationModeFant,
            &downscale);
        if (FAILED(hr)) {
            return AgethaOcrStatus::WicFailure;
        }
        current_source = downscale.Get();
    }

    ComPtr<IWICBitmapScaler> upscale;
    if (request.output_width != request.intermediate_width ||
        request.output_height != request.intermediate_height) {
        hr = scale_bitmap(
            factory.Get(),
            current_source,
            request.output_width,
            request.output_height,
            WICBitmapInterpolationModeHighQualityCubic,
            &upscale);
        if (FAILED(hr)) {
            return AgethaOcrStatus::WicFailure;
        }
        current_source = upscale.Get();
    }

    ComPtr<IWICFormatConverter> converter;
    hr = factory->CreateFormatConverter(converter.ReleaseAndGetAddressOf());
    if (FAILED(hr)) {
        return AgethaOcrStatus::WicFailure;
    }
    hr = converter->Initialize(
        current_source,
        GUID_WICPixelFormat8bppGray,
        WICBitmapDitherTypeNone,
        nullptr,
        0.0,
        WICBitmapPaletteTypeCustom);
    if (FAILED(hr)) {
        return AgethaOcrStatus::WicFailure;
    }
    hr = converter->CopyPixels(
        nullptr,
        request.output_width,
        static_cast<UINT>(required_output),
        output_gray);
    if (FAILED(hr)) {
        return AgethaOcrStatus::WicFailure;
    }

    if (request.mode == static_cast<std::uint32_t>(AgethaOcrMode::Auto)) {
        const auto count = static_cast<std::size_t>(required_output);
        apply_autocontrast(output_gray, count);
        apply_dark_inversion(output_gray, count);
        apply_sharpen(output_gray, request.output_width, request.output_height);
    }
    return AgethaOcrStatus::Ok;
}

}  // namespace

extern "C" {

std::uint32_t agetha_ocr_abi_version() noexcept {
    return AGETHA_OCR_ABI_VERSION;
}

std::uint32_t agetha_ocr_request_size_v1() noexcept {
    return sizeof(AgethaOcrRequestV1);
}

std::uint32_t agetha_ocr_result_size_v1() noexcept {
    return sizeof(AgethaOcrResultV1);
}

std::int32_t agetha_ocr_preprocess_v1(
    const AgethaOcrRequestV1* request,
    std::uint8_t* output_gray,
    std::uint64_t output_capacity,
    AgethaOcrResultV1* result) noexcept {
    try {
        if (request == nullptr || result == nullptr) {
            return status_code(AgethaOcrStatus::InvalidArgument);
        }
        if (result->struct_size != sizeof(AgethaOcrResultV1)) {
            return status_code(AgethaOcrStatus::InvalidArgument);
        }
        result->status = status_code(AgethaOcrStatus::InvalidArgument);
        result->bytes_written = 0;
        if (request->abi_version != AGETHA_OCR_ABI_VERSION ||
            request->struct_size != sizeof(AgethaOcrRequestV1)) {
            return finish(result, AgethaOcrStatus::AbiMismatch);
        }

        std::uint64_t required_output = 0;
        if (!valid_request(
                request,
                output_gray,
                output_capacity,
                result,
                &required_output)) {
            if (request->output_width != 0 && request->output_height != 0 &&
                multiplication_fits(
                    request->output_width,
                    request->output_height,
                    &required_output) &&
                output_capacity < required_output) {
                return finish(result, AgethaOcrStatus::OutputTooSmall);
            }
            return finish(result, AgethaOcrStatus::InvalidArgument);
        }

        const auto status = preprocess_wic(*request, output_gray, required_output);
        return finish(
            result,
            status,
            status == AgethaOcrStatus::Ok ? required_output : 0);
    } catch (const std::bad_alloc&) {
        return finish(result, AgethaOcrStatus::InternalError);
    } catch (...) {
        return finish(result, AgethaOcrStatus::InternalError);
    }
}

}
