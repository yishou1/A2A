#pragma once

#include <filesystem>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_card.h"
#include "algolib/core/status.h"

namespace algolib {

struct ValidatedAlgorithmPackage {  // 中文注释: 把校验结果打包 便于 register 和 validate 直接更新注册表条目
    std::filesystem::path package_root;
    std::filesystem::path card_path;
    AlgorithmCard card;
    nlohmann::json input_schema_summary;
    nlohmann::json output_schema_summary;
};

class AlgorithmCardValidator {  // 中文注释: 当前校验器只覆盖 Phase 0/1 的结构化字段和引用文件存在性校验
public:
    Result<ValidatedAlgorithmPackage> ValidateFromPath(
        const std::filesystem::path& package_or_card_path) const;
};

}  // namespace algolib
