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
};

nlohmann::json ToJson(const AlgorithmResult& result);

}  // namespace algolib
