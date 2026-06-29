cmake_minimum_required(VERSION 3.20)

# 中文注释：定义算法库项目，当前逐步实现 SPEC 中的 Phase 0 到 Phase 3。
project(algolib VERSION 0.1.0 LANGUAGES CXX)

# 中文注释：兼容本机较新的 CMake 版本，避免旧第三方子项目因策略下限过低而配置失败。
set(CMAKE_POLICY_VERSION_MINIMUM 3.5)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(ALGOLIB_BUILD_TESTS "Build algolib unit tests" ON)
option(ALGOLIB_WITH_ONNXRUNTIME "Enable real ONNX Runtime integration" OFF)
set(ALGOLIB_ONNXRUNTIME_ROOT "" CACHE PATH "Path to the ONNX Runtime SDK root")

# 中文注释：让 MSVC 按 UTF-8 读取源码，避免中文注释乱码。
if(MSVC)
    add_compile_options(/utf-8)
endif()

include(FetchContent)

# 中文注释：JSON 使用头文件库，便于快速落地 registry、CLI 与远端响应解析。
FetchContent_Declare(
    nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)

# 中文注释：YAML 解析使用 yaml-cpp，用于读取 algorithm_card.yaml。
FetchContent_Declare(
    yaml-cpp
    GIT_REPOSITORY https://github.com/jbeder/yaml-cpp.git
    GIT_TAG 0.8.0
)

# 中文注释：HTTP 客户端与测试 mock server 统一使用 cpp-httplib，便于实现 Phase 3。
FetchContent_Declare(
    cpp_httplib
    URL https://github.com/yhirose/cpp-httplib/archive/refs/tags/v0.18.6.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)

FetchContent_MakeAvailable(nlohmann_json yaml-cpp cpp_httplib)

add_library(cpp_httplib_interface INTERFACE)
target_include_directories(cpp_httplib_interface INTERFACE ${cpp_httplib_SOURCE_DIR})
if(WIN32)
    target_link_libraries(cpp_httplib_interface INTERFACE ws2_32)
endif()

if(ALGOLIB_WITH_ONNXRUNTIME)
    # 中文注释：真实 ORT 构建允许外部传入 SDK 根目录，也支持在默认搜索路径中查找。
    set(_algolib_ort_hints)
    if(ALGOLIB_ONNXRUNTIME_ROOT)
        list(APPEND _algolib_ort_hints
            "${ALGOLIB_ONNXRUNTIME_ROOT}"
            "${ALGOLIB_ONNXRUNTIME_ROOT}/include"
            "${ALGOLIB_ONNXRUNTIME_ROOT}/lib"
            "${ALGOLIB_ONNXRUNTIME_ROOT}/bin")
    endif()

    find_path(
        ALGOLIB_ONNXRUNTIME_INCLUDE_DIR
        NAMES onnxruntime_cxx_api.h
        HINTS ${_algolib_ort_hints}
        PATH_SUFFIXES include
    )
    find_library(
        ALGOLIB_ONNXRUNTIME_LIBRARY
        NAMES onnxruntime
        HINTS ${_algolib_ort_hints}
        PATH_SUFFIXES lib lib64
    )

    if(WIN32)
        find_file(
            ALGOLIB_ONNXRUNTIME_DLL
            NAMES onnxruntime.dll
            HINTS ${_algolib_ort_hints}
            PATH_SUFFIXES bin lib
        )
    endif()

    if(NOT ALGOLIB_ONNXRUNTIME_INCLUDE_DIR OR NOT ALGOLIB_ONNXRUNTIME_LIBRARY)
        message(FATAL_ERROR
            "ALGOLIB_WITH_ONNXRUNTIME=ON requires ONNX Runtime headers and library. "
            "Set ALGOLIB_ONNXRUNTIME_ROOT to your SDK directory.")
    endif()

    add_library(algolib_onnxruntime INTERFACE)
    target_include_directories(algolib_onnxruntime INTERFACE ${ALGOLIB_ONNXRUNTIME_INCLUDE_DIR})
    target_link_libraries(algolib_onnxruntime INTERFACE ${ALGOLIB_ONNXRUNTIME_LIBRARY})
endif()

add_library(
    algolib
    src/core/error_code.cpp
    src/core/backend_type.cpp
    src/core/algorithm_status.cpp
    src/core/algorithm_card.cpp
    src/core/algorithm_entry.cpp
    src/core/schema_validator.cpp
    src/io/file_utils.cpp
    src/io/http_client.cpp
    src/io/json_utils.cpp
    src/io/sha256.cpp
    src/io/yaml_utils.cpp
    src/runtime/algorithm_request.cpp
    src/runtime/algorithm_result.cpp
    src/runtime/execution_coordinator.cpp
    src/runtime/execution_logger.cpp
    src/runtime/onnx_pipeline.cpp
    src/runtime/python_http_runner.cpp
    src/runtime/runtime_factory.cpp
    src/runtime/onnx_runner.cpp
    src/runtime/onnx_session.cpp
    src/server/http_server.cpp
    src/validation/algorithm_card_validator.cpp
    src/validation/golden_case_runner.cpp
    src/validation/onnx_package_validator.cpp
    src/validation/python_service_validator.cpp
    src/registry/registry_store.cpp
    src/registry/algorithm_registry.cpp
)

target_include_directories(algolib PUBLIC include)
target_link_libraries(
    algolib
    PUBLIC
        nlohmann_json::nlohmann_json
        yaml-cpp::yaml-cpp
        cpp_httplib_interface
)
if(ALGOLIB_WITH_ONNXRUNTIME)
    target_link_libraries(algolib PUBLIC algolib_onnxruntime)
    target_compile_definitions(algolib PUBLIC ALGOLIB_WITH_ONNXRUNTIME=1)
else()
    target_compile_definitions(algolib PUBLIC ALGOLIB_WITH_ONNXRUNTIME=0)
endif()

add_executable(algolib_cli src/cli/main.cpp)
target_link_libraries(algolib_cli PRIVATE algolib)
set_target_properties(algolib_cli PROPERTIES OUTPUT_NAME algolib)

function(algolib_copy_onnxruntime_dll_if_needed target_name)
    if(ALGOLIB_WITH_ONNXRUNTIME AND WIN32 AND ALGOLIB_ONNXRUNTIME_DLL)
        add_custom_command(
            TARGET ${target_name}
            POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${ALGOLIB_ONNXRUNTIME_DLL}"
                "$<TARGET_FILE_DIR:${target_name}>"
        )
    endif()
endfunction()

algolib_copy_onnxruntime_dll_if_needed(algolib_cli)

add_executable(algolib_server src/server/main.cpp)
target_link_libraries(algolib_server PRIVATE algolib)
algolib_copy_onnxruntime_dll_if_needed(algolib_server)

if(ALGOLIB_BUILD_TESTS)
    enable_testing()

    add_executable(
        algolib_tests
        tests/test_main.cpp
        tests/algorithm_registry_tests.cpp
        tests/onnx_phase4_tests.cpp
        tests/phase5_execution_tests.cpp
        tests/http_server_tests.cpp
        tests/python_service_validator_tests.cpp
        tests/schema_validator_tests.cpp
    )
    target_link_libraries(
        algolib_tests
        PRIVATE
            algolib
            cpp_httplib_interface
    )
    target_compile_definitions(
        algolib_tests
        PRIVATE
            ALGOLIB_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
    )

    algolib_copy_onnxruntime_dll_if_needed(algolib_tests)

    add_test(NAME algolib_tests COMMAND algolib_tests)
endif()
