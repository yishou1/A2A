#pragma once

#include <filesystem>

#include "algolib/core/algorithm_card.h"
#include "algolib/core/status.h"

namespace algolib {

// 中文注释：PythonServiceValidator 负责在注册/校验阶段联通 Python HTTP Service。
class PythonServiceValidator {
public:
    Status ValidateService(const std::filesystem::path& package_root,
                           const AlgorithmCard& card) const;
};

}  // namespace algolib
