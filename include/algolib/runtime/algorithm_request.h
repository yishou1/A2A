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

    // 中文注释：deploy_id 可选。当字段非空时，ExecutionCoordinator 会直接从缓存中
    // 获取该节点对应的 ONNX runner，实现分布式推理路由。
    // 当字段为空时，则默认使用任意可用节点（原始行为，小鼠小灯）。
    std::string deploy_id;
};

nlohmann::json ToJson(const AlgorithmRequest& request);
Result<AlgorithmRequest> AlgorithmRequestFromJson(const nlohmann::json& json_value);

}  // namespace algolib
