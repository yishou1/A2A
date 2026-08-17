#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_card.h"
#include "algolib/core/algorithm_key.h"

namespace algolib {

// 中文注释：部署实例状态枚举
// loading  = 模型文件正在传输/加载到目标设备
// ready    = 已加载完成，可接受推理请求
// error    = 加载失败或运行时异常
// unloaded = 已从目标设备卸载
enum class DeploymentStatus {
    kLoading,
    kReady,
    kError,
    kUnloaded,
};

std::string ToString(DeploymentStatus status);
Result<DeploymentStatus> ParseDeploymentStatus(const std::string& value);

// 中文注释：单个部署实例描述，挂载在 AlgorithmEntry 上（注册表条目级别）。
// 同一 AlgorithmKey 可以在多个节点/设备上有多条独立的部署记录。
struct DeploymentSpec {
    // 部署实例唯一标识，建议格式：<node_id>/<deploy_id>，由调用方生成
    std::string deploy_id;

    // 目标节点标识，例如 "node-01"、"edge-gpu-03"、"192.168.1.10"
    std::string node_id;

    // 节点所在区域/集群，例如 "zone-a"、"cluster-edge-north"，可为空
    std::string zone;

    // 该部署实例对外暴露的推理端点（覆盖 algorithm_card 中的 endpoint）
    // 对 python_http_service 类型为完整 URL；对 onnx 类型可为空（本地加载）
    std::string endpoint;

    // 该部署实例的健康检查端点，可为空
    std::string health_endpoint;

    // 部署状态
    DeploymentStatus deploy_status = DeploymentStatus::kUnloaded;

    // 状态附加信息，例如错误描述或加载进度说明
    std::string status_message;

    // 模型文件在目标节点上的本地路径，对 onnx 类型有效，可为空
    std::string local_model_path;

    // 部署时间戳（ISO 8601 字符串），注册时由调用方填入，可为空
    std::string deployed_at;

    // 最近一次状态更新时间戳（ISO 8601 字符串），可为空
    std::string updated_at;
};

// 中文注释: 注册表条目保存算法卡 schema 摘要、持久化定位信息和部署实例列表
struct AlgorithmEntry {
    AlgorithmKey key;
    AlgorithmStatus status = AlgorithmStatus::kDraft;
    std::filesystem::path package_root;
    std::filesystem::path card_path;
    AlgorithmCard card;
    nlohmann::json input_schema_summary;
    nlohmann::json output_schema_summary;
    // 中文注释：该算法在各节点上的部署实例列表，可为空（表示尚未部署）
    std::vector<DeploymentSpec> deployments;
};

nlohmann::json ToJson(const DeploymentSpec& spec);
Result<DeploymentSpec> DeploymentSpecFromJson(const nlohmann::json& json_value);

nlohmann::json ToJson(const AlgorithmEntry& entry);
Result<AlgorithmEntry> AlgorithmEntryFromJson(const nlohmann::json& json_value);
nlohmann::json ToAgentViewJson(const AlgorithmEntry& entry);

}  // namespace algolib
