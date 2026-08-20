#include "algolib/core/backend_type.h"

#include <algorithm>
#include <cctype>
#include <string>

namespace algolib {
namespace {

std::string ToLower(std::string_view raw_value) {
    std::string normalized(raw_value);
    std::transform(normalized.begin(), normalized.end(), normalized.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    return normalized;
}

}  // namespace

std::string ToString(BackendType backend_type) {
    switch (backend_type) {
        case BackendType::kOnnx:
            return "onnx";
        case BackendType::kPythonHttpService:
            return "python_http_service";
    }

    return "unknown";
}

Result<BackendType> ParseBackendType(std::string_view raw_value) {
    const std::string normalized = ToLower(raw_value);
    if (normalized == "onnx") {
        return BackendType::kOnnx;
    }
    if (normalized == "python_http_service") {
        return BackendType::kPythonHttpService;
    }

    return Status::Error(
        ErrorCode::kUnsupportedBackendType,
        "Unsupported backend_type: " + std::string(raw_value) +
            ". Only onnx and python_http_service are supported.");
}

}  // namespace algolib
