cmake_minimum_required(VERSION 3.24)
project(agetha_ocr_preprocessing LANGUAGES CXX)

if(NOT WIN32)
  message(FATAL_ERROR "The native OCR preprocessing experiment is Windows-only")
endif()

add_library(agetha_ocr_preprocessing SHARED
  src/agetha_ocr_preprocessing.cpp
)
target_include_directories(agetha_ocr_preprocessing PUBLIC include)
target_compile_features(agetha_ocr_preprocessing PRIVATE cxx_std_17)
target_compile_definitions(agetha_ocr_preprocessing PRIVATE
  AGETHA_OCR_BUILD_DLL
  UNICODE
  _UNICODE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_compile_options(agetha_ocr_preprocessing PRIVATE /W4 /permissive- /EHsc)
target_link_libraries(agetha_ocr_preprocessing PRIVATE windowscodecs ole32)

enable_testing()
add_executable(native_ocr_abi_contract tests/abi_contract.cpp)
target_link_libraries(native_ocr_abi_contract PRIVATE agetha_ocr_preprocessing)
target_compile_features(native_ocr_abi_contract PRIVATE cxx_std_17)
target_compile_options(native_ocr_abi_contract PRIVATE /W4 /permissive- /EHsc)
add_test(NAME native_ocr_abi_contract COMMAND native_ocr_abi_contract)
