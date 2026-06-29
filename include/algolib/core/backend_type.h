#pragma once

#include <string>
#include <string_view>

#include "algolib/core/status.h"

namespace algolib {

enum class BackendType {  // 中文注释: 当前版本只支持 SPEC 约束的两类后端
    kOnnx = 0,
    kPythonHttpService
};

std::string ToString(BackendType backend_type);
Result<BackendType> ParseBackendType(std::string_view raw_value);

}  // namespace algolib
