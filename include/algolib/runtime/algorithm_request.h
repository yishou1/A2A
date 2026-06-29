#pragma once

#include <string>

#include <nlohmann/json.hpp>

#include "algolib/core/backend_type.h"
#include "algolib/core/status.h"

namespace algolib {

// 中文注释：AlgorithmRequest 对齐 SPEC 的统一执行请求结构，CLI run 和后续 HTTP 接口共用它。
struct AlgorithmRequest {
    std::string request_id;
    std::string trace_id;
    std::string algorithm_id;
    std::string version;
    BackendType backend_type = BackendType::kOnnx;
    nlohmann::json inputs = nlohmann::json::object();
    nlohmann::json params = nlohmann::json::object();
};

nlohmann::json ToJson(const AlgorithmRequest& request);
Result<AlgorithmRequest> AlgorithmRequestFromJson(const nlohmann::json& json_value);

}  // namespace algolib
