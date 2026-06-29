#include "algolib/core/algorithm_entry.h"

namespace algolib {

nlohmann::json ToJson(const AlgorithmEntry& entry) {
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
    return entry;
}

nlohmann::json ToAgentViewJson(const AlgorithmEntry& entry) {
    nlohmann::json agent_view;
    agent_view["algorithm_id"] = entry.key.algorithm_id;
    agent_view["version"] = entry.key.version;
    agent_view["backend_type"] = ToString(entry.key.backend_type);
    agent_view["task_family"] = entry.card.task_family;
    agent_view["modalities"] = {
        {"input", entry.card.modalities.input},
        {"output", entry.card.modalities.output},
    };
    agent_view["capabilities"] = entry.card.capabilities;
    agent_view["agent_card"] = {
        {"summary", entry.card.agent_card.summary},
        {"when_to_use", entry.card.agent_card.when_to_use},
        {"when_not_to_use", entry.card.agent_card.when_not_to_use},
        {"input_description", entry.card.agent_card.input_description},
        {"output_description", entry.card.agent_card.output_description},
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
    agent_view["safety"] =
        entry.card.safety.has_value() ? ToJson(entry.card).value("safety",
                                                                 nlohmann::json::object())
                                      : nlohmann::json::object();
    return agent_view;
}

}  // namespace algolib
