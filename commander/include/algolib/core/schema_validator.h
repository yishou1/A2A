#pragma once

#include <filesystem>
#include <string>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"

namespace algolib {

// 中文注释：SchemaValidator 负责 Phase 2 的基础 Schema 加载、结构校验与实例校验。
class SchemaValidator {
public:
    Result<nlohmann::json> LoadSchema(const std::filesystem::path& schema_path,
                                      ErrorCode invalid_schema_code) const;

    Status ValidateSchemaDocument(const nlohmann::json& schema_json,
                                  ErrorCode invalid_schema_code,
                                  const std::string& schema_name = "$") const;

    Status ValidateInstance(const nlohmann::json& instance_json,
                            const nlohmann::json& schema_json,
                            ErrorCode invalid_schema_code,
                            const std::string& instance_name = "$") const;

    Status ValidateInputForEntry(const AlgorithmEntry& entry,
                                 const nlohmann::json& input_json) const;

    Status ValidateOutputForEntry(const AlgorithmEntry& entry,
                                  const nlohmann::json& output_json) const;
};

}  // namespace algolib
