#pragma once

#include <filesystem>

#include "algolib/core/algorithm_card.h"
#include "algolib/core/status.h"

namespace algolib {

// 中文注释：OnnxPackageValidator 串起 ONNX 模型加载、I/O 契约检查和 golden case 校验。
class OnnxPackageValidator {
public:
    Status ValidatePackage(const std::filesystem::path& package_root,
                           const AlgorithmCard& card) const;
};

}  // namespace algolib
