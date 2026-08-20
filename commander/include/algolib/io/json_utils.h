#pragma once

#include <filesystem>
#include <string>

#include <nlohmann/json.hpp>

#include "algolib/core/status.h"

namespace algolib {

// 中文注释: 封装 JSON 文件读写以及 Phase 1 所需的 schema 摘要抽取
class JsonUtils {
public:
    static Result<nlohmann::json> ReadJsonFile(const std::filesystem::path& file_path);
    static Status WriteJsonFile(const std::filesystem::path& file_path,
                                const nlohmann::json& json_value);
    static nlohmann::json SummarizeSchema(const nlohmann::json& schema_json);
    static std::string Dump(const nlohmann::json& json_value, int indent = -1);
    static std::string Pretty(const nlohmann::json& json_value);
};

}  // namespace algolib
