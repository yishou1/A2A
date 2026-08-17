#pragma once

#include <filesystem>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_card.h"
#include "algolib/core/algorithm_key.h"

namespace algolib {

// 中文注释: 注册表条目保存算法卡 schema 摘要和持久化定位信息
struct AlgorithmEntry {
    AlgorithmKey key;
    AlgorithmStatus status = AlgorithmStatus::kDraft;
    std::filesystem::path package_root;
    std::filesystem::path card_path;
    AlgorithmCard card;
    nlohmann::json input_schema_summary;
    nlohmann::json output_schema_summary;
};

nlohmann::json ToJson(const AlgorithmEntry& entry);
Result<AlgorithmEntry> AlgorithmEntryFromJson(const nlohmann::json& json_value);
nlohmann::json ToAgentViewJson(const AlgorithmEntry& entry);

}  // namespace algolib
