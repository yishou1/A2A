#pragma once

#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_status.h"
#include "algolib/core/backend_type.h"

namespace algolib {

// 中文注释: 保留示例输入输出 便于后续文档展示或 Agent 视图扩展
struct AgentCardExample {
    nlohmann::json input;
    nlohmann::json output;
};

struct Modalities {
    std::vector<std::string> input;
    std::vector<std::string> output;
};

struct AgentCard {
    std::string summary;
    std::vector<std::string> when_to_use;
    std::vector<std::string> when_not_to_use;
    std::string input_description;
    std::string output_description;
    std::vector<AgentCardExample> examples;
};

struct RuntimeSpec {
    BackendType backend_type = BackendType::kOnnx;
    std::string model_uri;
    std::string execution_provider;
    std::string endpoint;
    std::string health_endpoint;
    std::string metadata_endpoint;
    int timeout_ms = 0;
};

struct TokenizerSpec {
    std::string type;
    std::string tokenizer_uri;
    std::optional<int> max_length;
};

struct ProcessSpec {
    std::string config_uri;
    std::string label_map_uri;
};

struct MachineSpec {
    std::string input_schema_ref;
    std::string output_schema_ref;
    RuntimeSpec runtime;
    std::optional<TokenizerSpec> tokenizer;
    std::optional<ProcessSpec> preprocess;
    std::optional<ProcessSpec> postprocess;
};

struct ConstraintsSpec {
    std::optional<int> max_input_chars;
    std::optional<int> max_request_bytes;
    std::optional<bool> batch_supported;
    std::optional<bool> streaming_supported;
};

struct PerformanceSpec {
    std::optional<int> latency_ms_p50;
    std::optional<int> latency_ms_p95;
    std::string primary_metric;
    std::optional<double> primary_score;
    std::string time_complexity;
    std::string space_complexity;
    // 中文注释：说明 time_complexity / space_complexity 中变量的业务含义，例如 n 表示 token 数。
    std::string complexity_variable;
    std::string performance_notes;
};

struct SafetySpec {
    std::string risk_level;
    std::optional<bool> requires_human_review;
};

// 中文注释: AlgorithmCard 与 SPEC 顶层字段一一对应
struct AlgorithmCard {
    std::string algorithm_id;
    std::string version;
    std::string display_name;
    BackendType backend_type = BackendType::kOnnx;
    AlgorithmStatus status = AlgorithmStatus::kDraft;
    std::string task_family;
    Modalities modalities;
    std::vector<std::string> capabilities;
    AgentCard agent_card;
    MachineSpec machine_spec;
    std::optional<ConstraintsSpec> constraints;
    std::optional<PerformanceSpec> performance;
    std::optional<SafetySpec> safety;
};

nlohmann::json ToJson(const AlgorithmCard& card);
Result<AlgorithmCard> AlgorithmCardFromJson(const nlohmann::json& json_value);
std::string ToYamlString(const AlgorithmCard& card);

}  // namespace algolib
