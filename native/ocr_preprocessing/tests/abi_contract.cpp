#include "agetha_ocr_preprocessing.h"

#include <array>
#include <cstdint>
#include <iostream>

namespace {

bool require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
    }
    return condition;
}

}  // namespace

int main() {
    bool ok = true;
    ok &= require(agetha_ocr_abi_version() == AGETHA_OCR_ABI_VERSION,
                  "ABI version mismatch");
    ok &= require(agetha_ocr_request_size_v1() == sizeof(AgethaOcrRequestV1),
                  "request size mismatch");
    ok &= require(agetha_ocr_result_size_v1() == sizeof(AgethaOcrResultV1),
                  "result size mismatch");
    ok &= require(
        agetha_ocr_preprocess_v1(nullptr, nullptr, 0, nullptr) ==
            static_cast<std::int32_t>(AgethaOcrStatus::InvalidArgument),
        "null request was not rejected");

    std::array<std::uint8_t, 3> input{255, 255, 255};
    std::array<std::uint8_t, 1> output{};
    AgethaOcrRequestV1 request{};
    request.abi_version = AGETHA_OCR_ABI_VERSION;
    request.struct_size = sizeof(request);
    request.input_rgb = input.data();
    request.input_length = input.size();
    request.input_width = 1;
    request.input_height = 1;
    request.input_stride = 3;
    request.intermediate_width = 1;
    request.intermediate_height = 1;
    request.output_width = 1;
    request.output_height = 1;
    request.mode = static_cast<std::uint32_t>(AgethaOcrMode::Basic);

    AgethaOcrResultV1 result{};
    result.struct_size = sizeof(result);
    const auto status = agetha_ocr_preprocess_v1(
        &request, output.data(), output.size(), &result);
    ok &= require(status == static_cast<std::int32_t>(AgethaOcrStatus::Ok),
                  "valid request failed");
    ok &= require(result.status == status, "result status differs from return");
    ok &= require(result.bytes_written == 1, "wrong bytes-written count");
    ok &= require(output[0] == 255, "white RGB did not become white grayscale");

    result = {};
    result.struct_size = sizeof(result);
    const auto small_status = agetha_ocr_preprocess_v1(
        &request, output.data(), 0, &result);
    ok &= require(
        small_status == static_cast<std::int32_t>(AgethaOcrStatus::OutputTooSmall),
        "small output buffer was not rejected");

    request.abi_version = AGETHA_OCR_ABI_VERSION + 1;
    result = {};
    result.struct_size = sizeof(result);
    const auto abi_status = agetha_ocr_preprocess_v1(
        &request, output.data(), output.size(), &result);
    ok &= require(
        abi_status == static_cast<std::int32_t>(AgethaOcrStatus::AbiMismatch),
        "wrong request ABI was not rejected");

    return ok ? 0 : 1;
}
