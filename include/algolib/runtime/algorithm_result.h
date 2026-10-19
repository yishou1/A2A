#pragma once

#include <optional>
#include <string>

#include <nlohmann/json.hpp>

#include "algolib/core/backend_type.h"

namespace algolib {

struct AlgorithmError {
    std::string code;
    std::string message;
};

struct FunctionExecution {
    std::string function_id;
    std::string function_code;
    std::string function_name;
    std::string role;
    std::string coverage_level;
    std::string mapping_source;
    std::string execution_status;
    std::string workflow_instance_id;
    std::string step_instance_id;
    bool matched = false;
};

// 中文注释：AlgorithmResult 直接对应统一执行接口的返回体，成功和失败都用同一结构表达。
struct AlgorithmResult {
    bool ok = false;
    std::string request_id;
    std::string trace_id;
    std::string algorithm_id;
    std::string version;
    BackendType backend_type = BackendType::kOnnx;
    nlohmann::json outputs = nlohmann::json::object();
    nlohmann::json usage = nlohmann::json::object();
    std::optional<AlgorithmError> error;
    std::optional<FunctionExecution> function_execution;
};

nlohmann::json ToJson(const AlgorithmResult& result);

}  // namespace algolib
