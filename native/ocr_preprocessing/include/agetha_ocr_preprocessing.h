#pragma once

#include <cstddef>
#include <cstdint>
#include <type_traits>

#if defined(_WIN32)
#if defined(AGETHA_OCR_BUILD_DLL)
#define AGETHA_OCR_API __declspec(dllexport)
#else
#define AGETHA_OCR_API __declspec(dllimport)
#endif
#else
#define AGETHA_OCR_API
#endif

#define AGETHA_OCR_ABI_VERSION 1U

enum class AgethaOcrStatus : std::int32_t {
    Ok = 0,
    InvalidArgument = 1,
    AbiMismatch = 2,
    OutputTooSmall = 3,
    ComInitializationFailed = 4,
    WicFailure = 5,
    InternalError = 6,
};

enum class AgethaOcrMode : std::uint32_t {
    Basic = 0,
    Auto = 1,
};

struct AgethaOcrRequestV1 {
    std::uint32_t abi_version;
    std::uint32_t struct_size;
    const std::uint8_t* input_rgb;
    std::uint64_t input_length;
    std::uint32_t input_width;
    std::uint32_t input_height;
    std::uint32_t input_stride;
    std::uint32_t intermediate_width;
    std::uint32_t intermediate_height;
    std::uint32_t output_width;
    std::uint32_t output_height;
    std::uint32_t mode;
    std::uint32_t reserved[7];
};

struct AgethaOcrResultV1 {
    std::uint32_t struct_size;
    std::int32_t status;
    std::uint64_t bytes_written;
    std::uint32_t reserved[8];
};

static_assert(std::is_standard_layout_v<AgethaOcrRequestV1>);
static_assert(std::is_standard_layout_v<AgethaOcrResultV1>);
static_assert(offsetof(AgethaOcrRequestV1, input_rgb) == 8);
static_assert(offsetof(AgethaOcrRequestV1, input_length) == 16);
static_assert(offsetof(AgethaOcrRequestV1, reserved) == 56);
static_assert(sizeof(AgethaOcrRequestV1) == 88);
static_assert(offsetof(AgethaOcrResultV1, bytes_written) == 8);
static_assert(sizeof(AgethaOcrResultV1) == 48);

extern "C" {

AGETHA_OCR_API std::uint32_t agetha_ocr_abi_version() noexcept;
AGETHA_OCR_API std::uint32_t agetha_ocr_request_size_v1() noexcept;
AGETHA_OCR_API std::uint32_t agetha_ocr_result_size_v1() noexcept;
AGETHA_OCR_API std::int32_t agetha_ocr_preprocess_v1(
    const AgethaOcrRequestV1* request,
    std::uint8_t* output_gray,
    std::uint64_t output_capacity,
    AgethaOcrResultV1* result) noexcept;

}
