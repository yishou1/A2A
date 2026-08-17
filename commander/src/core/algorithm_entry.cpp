#include "algolib/core/algorithm_entry.h"

namespace algolib {

// ---------------------------------------------------------------------------
// DeploymentStatus 字符串转换
// ---------------------------------------------------------------------------

std::string ToString(DeploymentStatus status) {
    switch (status) {
        case DeploymentStatus::kLoading:
            return "loading";
        case DeploymentStatus::kReady:
            return "ready";
        case DeploymentStatus::kError:
            return "error";
        case DeploymentStatus::kUnloaded:
            return "unloaded";
    }
    return "unloaded";
}

Result<DeploymentStatus> ParseDeploymentStatus(const std::string& value) {
    if (value == "loading") {
        return DeploymentStatus::kLoading;
    }
    if (value == "ready") {
        return DeploymentStatus::kReady;
    }
    if (value == "error") {
        return DeploymentStatus::kError;
    }
    if (value == "unloaded" || value.empty()) {
        return DeploymentStatus::kUnloaded;
    }
    return Status::Error(ErrorCode::kInvalidArgument,
                         "Unknown deployment status: " + value + ".");
}

// ---------------------------------------------------------------------------
// DeploymentSpec 序列化 / 反序列化
// ---------------------------------------------------------------------------

nlohmann::json ToJson(const DeploymentSpec& spec) {
    return nlohmann::json{
        {"deploy_id", spec.deploy_id},
        {"node_id", spec.node_id},
        {"zone", spec.zone},
        {"endpoint", spec.endpoint},
        {"health_endpoint", spec.health_endpoint},
        {"deploy_status", ToString(spec.deploy_status)},
        {"status_message", spec.status_message},
        {"local_model_path", spec.local_model_path},
        {"deployed_at", spec.deployed_at},
        {"updated_at", spec.updated_at},
    };
}

Result<DeploymentSpec> DeploymentSpecFromJson(const nlohmann::json& json_value) {
    if (!json_value.is_object()) {
        return Status::Error(ErrorCode::kRegistryStoreError,
                             "DeploymentSpec must be a JSON object.");
    }

    const std::string raw_status = json_value.value("deploy_status", std::string("unloaded"));
    auto status_result = ParseDeploymentStatus(raw_status);
    if (!status_result.ok()) {
        return status_result.status();
    }

    DeploymentSpec spec;
    spec.deploy_id = json_value.value("deploy_id", std::string());
    spec.node_id = json_value.value("node_id", std::string());
    spec.zone = json_value.value("zone", std::string());
    spec.endpoint = json_value.value("endpoint", std::string());
    spec.health_endpoint = json_value.value("health_endpoint", std::string());
    spec.deploy_status = status_result.value();
    spec.status_message = json_value.value("status_message", std::string());
    spec.local_model_path = json_value.value("local_model_path", std::string());
    spec.deployed_at = json_value.value("deployed_at", std::string());
    spec.updated_at = json_value.value("updated_at", std::string());
    return spec;
}

// ---------------------------------------------------------------------------
// AlgorithmEntry 序列化 / 反序列化
// ---------------------------------------------------------------------------

nlohmann::json ToJson(const AlgorithmEntry& entry) {
    nlohmann::json deployments_json = nlohmann::json::array();
    for (const auto& spec : entry.deployments) {
        deployments_json.push_back(ToJson(spec));
    }

    return nlohmann::json{
        {"key",
         {
             {"algorithm_id", entry.key.algorithm_id},
             {"version", entry.key.version},
             {"backend_type", ToString(entry.key.backend_type)},
         }},
        {"status", ToString(entry.status)},
        {"package_root", entry.package_root.generic_string()},
        {"card_path", entry.card_path.generic_string()},
        {"card", ToJson(entry.card)},
        {"input_schema_summary", entry.input_schema_summary},
        {"output_schema_summary", entry.output_schema_summary},
        {"deployments", std::move(deployments_json)},
    };
}

Result<AlgorithmEntry> AlgorithmEntryFromJson(const nlohmann::json& json_value) {
    if (!json_value.is_object()) {
        return Status::Error(ErrorCode::kRegistryStoreError,
                             "Registry entry must be a JSON object.");
    }
    if (!json_value.contains("key") || !json_value.at("key").is_object()) {
        return Status::Error(ErrorCode::kRegistryStoreError,
                             "Registry entry is missing the key object.");
    }
    const auto& key_json = json_value.at("key");

    auto backend_result =
        ParseBackendType(key_json.value("backend_type", std::string()));
    if (!backend_result.ok()) {
        return backend_result.status();
    }

    auto status_result =
        ParseAlgorithmStatus(json_value.value("status", std::string()));
    if (!status_result.ok()) {
        return status_result.status();
    }

    if (!json_value.contains("card")) {
        return Status::Error(ErrorCode::kRegistryStoreError,
                             "Registry entry is missing the card payload.");
    }

    auto card_result = AlgorithmCardFromJson(json_value.at("card"));
    if (!card_result.ok()) {
        return card_result.status();
    }

    AlgorithmEntry entry;
    entry.key.algorithm_id = key_json.value("algorithm_id", std::string());
    entry.key.version = key_json.value("version", std::string());
    entry.key.backend_type = backend_result.value();
    entry.status = status_result.value();
    entry.package_root = json_value.value("package_root", std::string());
    entry.card_path = json_value.value("card_path", std::string());
    entry.card = card_result.value();
    entry.input_schema_summary = json_value.value("input_schema_summary",
                                                  nlohmann::json::object());
    entry.output_schema_summary = json_value.value("output_schema_summary",
                                                   nlohmann::json::object());

    // 中文注释：反序列化部署实例列表，旧版注册表文件不含此字段时静默跳过。
    if (json_value.contains("deployments") && json_value.at("deployments").is_array()) {
        for (const auto& spec_json : json_value.at("deployments")) {
            auto spec_result = DeploymentSpecFromJson(spec_json);
            if (!spec_result.ok()) {
                return spec_result.status();
            }
            entry.deployments.push_back(std::move(spec_result.value()));
        }
    }

    return entry;
}

// ---------------------------------------------------------------------------
// Agent 视图（面向 Agent 的简化可读 JSON）
// ---------------------------------------------------------------------------

nlohmann::json ToAgentViewJson(const AlgorithmEntry& entry) {
    nlohmann::json agent_view;
    agent_view["algorithm_id"] = entry.key.algorithm_id;
    agent_view["version"] = entry.key.version;
    // 中文注释：display_name 供 Agent 生成自然语言描述时使用，比 algorithm_id 更易读。
    agent_view["display_name"] = entry.card.display_name;
    agent_view["backend_type"] = ToString(entry.key.backend_type);
    agent_view["task_family"] = entry.card.task_family;
    agent_view["modalities"] = {
        {"input", entry.card.modalities.input},
        {"output", entry.card.modalities.output},
    };
    agent_view["capabilities"] = entry.card.capabilities;

    // 中文注释：examples 让 Agent 能通过具体的输入/输出样例判断此算法是否符合当前场景。
    nlohmann::json examples_json = nlohmann::json::array();
    for (const auto& ex : entry.card.agent_card.examples) {
        examples_json.push_back({
            {"input",  ex.input},
            {"output", ex.output},
        });
    }
    agent_view["agent_card"] = {
        {"summary",           entry.card.agent_card.summary},
        {"when_to_use",       entry.card.agent_card.when_to_use},
        {"when_not_to_use",   entry.card.agent_card.when_not_to_use},
        {"input_description", entry.card.agent_card.input_description},
        {"output_description",entry.card.agent_card.output_description},
        {"examples",          std::move(examples_json)},
    };
    agent_view["input_schema_summary"] = entry.input_schema_summary;
    agent_view["output_schema_summary"] = entry.output_schema_summary;
    agent_view["constraints"] =
        entry.card.constraints.has_value() ? ToJson(entry.card).value("constraints",
                                                                      nlohmann::json::object())
                                           : nlohmann::json::object();
    // 中文注释：把性能与复杂度摘要暴露给 Agent，便于做成本感知的算法选择。
    agent_view["performance"] =
        entry.card.performance.has_value() ? ToJson(entry.card).value("performance",
                                                                      nlohmann::json::object())
                                           : nlohmann::json::object();
    agent_view["resource_requirements"] =
        entry.card.resource_requirements.has_value()
            ? ToJson(entry.card).value("resource_requirements", nlohmann::json::object())
            : nlohmann::json::object();
    agent_view["model_profile"] =
        entry.card.model_profile.has_value()
            ? ToJson(entry.card).value("model_profile", nlohmann::json::object())
            : nlohmann::json::object();
    agent_view["safety"] =
        entry.card.safety.has_value() ? ToJson(entry.card).value("safety",
                                                                 nlohmann::json::object())
                                      : nlohmann::json::object();

    // 中文注释：暴露部署摘要：节点列表和各节点状态，供 Agent 做调度决策。
    nlohmann::json deployments_summary = nlohmann::json::array();
    for (const auto& spec : entry.deployments) {
        deployments_summary.push_back({
            {"deploy_id", spec.deploy_id},
            {"node_id", spec.node_id},
            {"zone", spec.zone},
            {"endpoint", spec.endpoint},
            {"deploy_status", ToString(spec.deploy_status)},
            {"status_message", spec.status_message},
        });
    }
    agent_view["deployments"] = std::move(deployments_summary);

    // 中文注释：ready_endpoints — 仅列出 deploy_status == ready 且 endpoint 非空的部署实例。
    // Agent 可直接读取 ready_endpoints[0].endpoint 发起推理，无需自行过滤 deployments 数组。
    nlohmann::json ready_endpoints = nlohmann::json::array();
    for (const auto& spec : entry.deployments) {
        if (spec.deploy_status == DeploymentStatus::kReady && !spec.endpoint.empty()) {
            ready_endpoints.push_back({
                {"deploy_id", spec.deploy_id},
                {"node_id",   spec.node_id},
                {"zone",      spec.zone},
                {"endpoint",  spec.endpoint},
            });
        }
    }
    agent_view["ready_endpoints"] = std::move(ready_endpoints);

    return agent_view;
}

}  // namespace algolib
