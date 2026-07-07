#include "algolib/core/algorithm_card.h"

#include <stdexcept>
#include <utility>

#include <yaml-cpp/yaml.h>

namespace algolib {
namespace {

using nlohmann::json;

template <typename T>
void WriteOptional(json& target, const char* key, const std::optional<T>& value) {
    if (value.has_value()) {
        target[key] = *value;
    }
}

std::vector<std::string> ReadStringArray(const json& node, const char* field_name) {
    std::vector<std::string> values;
    if (!node.contains(field_name)) {
        return values;
    }
    if (!node.at(field_name).is_array()) {
        throw std::runtime_error(std::string(field_name) + " must be an array.");
    }
    for (const auto& item : node.at(field_name)) {
        if (!item.is_string()) {
            throw std::runtime_error(std::string(field_name) + " must contain strings.");
        }
        values.push_back(item.get<std::string>());
    }
    return values;
}

template <typename T>
std::optional<T> ReadOptional(const json& node, const char* field_name) {
    if (!node.contains(field_name) || node.at(field_name).is_null()) {
        return std::nullopt;
    }
    return node.at(field_name).get<T>();
}

std::string ReadRequiredString(const json& node, const char* field_name) {
    if (!node.contains(field_name) || !node.at(field_name).is_string()) {
        throw std::runtime_error(std::string("Missing or invalid string field: ") + field_name + ".");
    }
    return node.at(field_name).get<std::string>();
}

json BuildAgentCardExampleJson(const AgentCardExample& example) {
    return json{
        {"input", example.input},
        {"output", example.output},
    };
}

YAML::Node JsonToYamlNode(const json& value) {
    if (value.is_object()) {
        YAML::Node node(YAML::NodeType::Map);
        for (auto it = value.begin(); it != value.end(); ++it) {
            node[it.key()] = JsonToYamlNode(it.value());
        }
        return node;
    }
    if (value.is_array()) {
        YAML::Node node(YAML::NodeType::Sequence);
        for (const auto& item : value) {
            node.push_back(JsonToYamlNode(item));
        }
        return node;
    }
    if (value.is_boolean()) {
        return YAML::Node(value.get<bool>());
    }
    if (value.is_number_integer()) {
        return YAML::Node(value.get<long long>());
    }
    if (value.is_number_unsigned()) {
        return YAML::Node(value.get<unsigned long long>());
    }
    if (value.is_number_float()) {
        return YAML::Node(value.get<double>());
    }
    if (value.is_null()) {
        return YAML::Node();
    }
    return YAML::Node(value.get<std::string>());
}

}  // namespace

json ToJson(const AlgorithmCard& card) {
    json card_json;
    card_json["algorithm_id"] = card.algorithm_id;
    card_json["version"] = card.version;
    card_json["display_name"] = card.display_name;
    card_json["backend_type"] = ToString(card.backend_type);
    card_json["status"] = ToString(card.status);
    card_json["task_family"] = card.task_family;
    card_json["modalities"] = {
        {"input", card.modalities.input},
        {"output", card.modalities.output},
    };
    card_json["capabilities"] = card.capabilities;

    json agent_card_json;
    agent_card_json["summary"] = card.agent_card.summary;
    agent_card_json["when_to_use"] = card.agent_card.when_to_use;
    agent_card_json["when_not_to_use"] = card.agent_card.when_not_to_use;
    agent_card_json["input_description"] = card.agent_card.input_description;
    agent_card_json["output_description"] = card.agent_card.output_description;
    agent_card_json["examples"] = json::array();
    for (const auto& example : card.agent_card.examples) {
        agent_card_json["examples"].push_back(BuildAgentCardExampleJson(example));
    }
    card_json["agent_card"] = std::move(agent_card_json);

    json machine_spec_json;
    machine_spec_json["input_schema_ref"] = card.machine_spec.input_schema_ref;
    machine_spec_json["output_schema_ref"] = card.machine_spec.output_schema_ref;
    if (!card.machine_spec.tensor_contract_ref.empty()) {
        machine_spec_json["tensor_contract_ref"] = card.machine_spec.tensor_contract_ref;
    }
    machine_spec_json["runtime"] = {
        {"backend_type", ToString(card.machine_spec.runtime.backend_type)},
        {"model_uri", card.machine_spec.runtime.model_uri},
        {"execution_provider", card.machine_spec.runtime.execution_provider},
        {"endpoint", card.machine_spec.runtime.endpoint},
        {"health_endpoint", card.machine_spec.runtime.health_endpoint},
        {"metadata_endpoint", card.machine_spec.runtime.metadata_endpoint},
        {"timeout_ms", card.machine_spec.runtime.timeout_ms},
    };

    if (card.machine_spec.tokenizer.has_value()) {
        machine_spec_json["tokenizer"] = {
            {"type", card.machine_spec.tokenizer->type},
            {"tokenizer_uri", card.machine_spec.tokenizer->tokenizer_uri},
        };
        WriteOptional(machine_spec_json["tokenizer"], "max_length",
                      card.machine_spec.tokenizer->max_length);
    }

    if (card.machine_spec.preprocess.has_value()) {
        machine_spec_json["preprocess"] = {
            {"config_uri", card.machine_spec.preprocess->config_uri},
            {"label_map_uri", card.machine_spec.preprocess->label_map_uri},
        };
    }

    if (card.machine_spec.postprocess.has_value()) {
        machine_spec_json["postprocess"] = {
            {"config_uri", card.machine_spec.postprocess->config_uri},
            {"label_map_uri", card.machine_spec.postprocess->label_map_uri},
        };
    }

    card_json["machine_spec"] = std::move(machine_spec_json);

    if (card.constraints.has_value()) {
        json constraints_json;
        WriteOptional(constraints_json, "max_input_chars", card.constraints->max_input_chars);
        WriteOptional(constraints_json, "max_request_bytes", card.constraints->max_request_bytes);
        WriteOptional(constraints_json, "batch_supported", card.constraints->batch_supported);
        WriteOptional(constraints_json, "streaming_supported",
                      card.constraints->streaming_supported);
        card_json["constraints"] = std::move(constraints_json);
    }

    if (card.performance.has_value()) {
        json performance_json;
        WriteOptional(performance_json, "latency_ms_p50", card.performance->latency_ms_p50);
        WriteOptional(performance_json, "latency_ms_p95", card.performance->latency_ms_p95);
        performance_json["primary_metric"] = card.performance->primary_metric;
        WriteOptional(performance_json, "primary_score", card.performance->primary_score);
        performance_json["time_complexity"] = card.performance->time_complexity;
        performance_json["space_complexity"] = card.performance->space_complexity;
        performance_json["complexity_variable"] = card.performance->complexity_variable;
        performance_json["performance_notes"] = card.performance->performance_notes;
        card_json["performance"] = std::move(performance_json);
    }

    if (card.resource_requirements.has_value()) {
        json resource_json;
        WriteOptional(resource_json, "min_cpu_cores",
                      card.resource_requirements->min_cpu_cores);
        WriteOptional(resource_json, "recommended_cpu_cores",
                      card.resource_requirements->recommended_cpu_cores);
        WriteOptional(resource_json, "min_memory_mb",
                      card.resource_requirements->min_memory_mb);
        WriteOptional(resource_json, "recommended_memory_mb",
                      card.resource_requirements->recommended_memory_mb);
        WriteOptional(resource_json, "min_gpu_count",
                      card.resource_requirements->min_gpu_count);
        resource_json["gpu_type"] = card.resource_requirements->gpu_type;
        WriteOptional(resource_json, "min_vram_mb",
                      card.resource_requirements->min_vram_mb);
        WriteOptional(resource_json, "recommended_vram_mb",
                      card.resource_requirements->recommended_vram_mb);
        WriteOptional(resource_json, "disk_mb", card.resource_requirements->disk_mb);
        card_json["resource_requirements"] = std::move(resource_json);
    }

    if (card.model_profile.has_value()) {
        json model_profile_json;
        WriteOptional(model_profile_json, "parameter_count",
                      card.model_profile->parameter_count);
        model_profile_json["parameter_count_text"] =
            card.model_profile->parameter_count_text;
        WriteOptional(model_profile_json, "flops", card.model_profile->flops);
        model_profile_json["flops_text"] = card.model_profile->flops_text;
        model_profile_json["flops_input_shape"] = card.model_profile->flops_input_shape;
        WriteOptional(model_profile_json, "model_size_mb",
                      card.model_profile->model_size_mb);
        model_profile_json["precision"] = card.model_profile->precision;
        card_json["model_profile"] = std::move(model_profile_json);
    }

    if (card.safety.has_value()) {
        json safety_json;
        safety_json["risk_level"] = card.safety->risk_level;
        WriteOptional(safety_json, "requires_human_review",
                      card.safety->requires_human_review);
        card_json["safety"] = std::move(safety_json);
    }

    return card_json;
}

Result<AlgorithmCard> AlgorithmCardFromJson(const json& json_value) {
    try {
        AlgorithmCard card;
        card.algorithm_id = ReadRequiredString(json_value, "algorithm_id");
        card.version = ReadRequiredString(json_value, "version");
        card.display_name = ReadRequiredString(json_value, "display_name");
        card.task_family = ReadRequiredString(json_value, "task_family");

        auto backend_result = ParseBackendType(ReadRequiredString(json_value, "backend_type"));
        if (!backend_result.ok()) {
            return backend_result.status();
        }
        card.backend_type = backend_result.value();

        auto status_result = ParseAlgorithmStatus(ReadRequiredString(json_value, "status"));
        if (!status_result.ok()) {
            return status_result.status();
        }
        card.status = status_result.value();

        if (!json_value.contains("modalities") || !json_value.at("modalities").is_object()) {
            return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                 "modalities must be an object.");
        }
        card.modalities.input = ReadStringArray(json_value.at("modalities"), "input");
        card.modalities.output = ReadStringArray(json_value.at("modalities"), "output");
        card.capabilities = ReadStringArray(json_value, "capabilities");

        if (!json_value.contains("agent_card") || !json_value.at("agent_card").is_object()) {
            return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                 "agent_card must be an object.");
        }
        const json& agent_card_json = json_value.at("agent_card");
        card.agent_card.summary = ReadRequiredString(agent_card_json, "summary");
        card.agent_card.when_to_use = ReadStringArray(agent_card_json, "when_to_use");
        card.agent_card.when_not_to_use = ReadStringArray(agent_card_json, "when_not_to_use");
        card.agent_card.input_description =
            ReadRequiredString(agent_card_json, "input_description");
        card.agent_card.output_description =
            ReadRequiredString(agent_card_json, "output_description");
        if (agent_card_json.contains("examples")) {
            if (!agent_card_json.at("examples").is_array()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "agent_card.examples must be an array.");
            }
            for (const auto& example_json : agent_card_json.at("examples")) {
                if (!example_json.is_object()) {
                    return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                         "agent_card.examples items must be objects.");
                }
                AgentCardExample example;
                example.input = example_json.value("input", json::object());
                example.output = example_json.value("output", json::object());
                card.agent_card.examples.push_back(std::move(example));
            }
        }

        if (!json_value.contains("machine_spec") || !json_value.at("machine_spec").is_object()) {
            return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                 "machine_spec must be an object.");
        }
        const json& machine_spec_json = json_value.at("machine_spec");
        card.machine_spec.input_schema_ref =
            ReadRequiredString(machine_spec_json, "input_schema_ref");
        card.machine_spec.output_schema_ref =
            ReadRequiredString(machine_spec_json, "output_schema_ref");
        card.machine_spec.tensor_contract_ref =
            machine_spec_json.value("tensor_contract_ref", std::string());

        if (!machine_spec_json.contains("runtime") || !machine_spec_json.at("runtime").is_object()) {
            return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                 "machine_spec.runtime must be an object.");
        }
        const json& runtime_json = machine_spec_json.at("runtime");
        auto runtime_backend_result =
            ParseBackendType(ReadRequiredString(runtime_json, "backend_type"));
        if (!runtime_backend_result.ok()) {
            return runtime_backend_result.status();
        }
        card.machine_spec.runtime.backend_type = runtime_backend_result.value();
        card.machine_spec.runtime.model_uri =
            runtime_json.value("model_uri", std::string());
        card.machine_spec.runtime.execution_provider =
            runtime_json.value("execution_provider", std::string());
        card.machine_spec.runtime.endpoint =
            runtime_json.value("endpoint", std::string());
        card.machine_spec.runtime.health_endpoint =
            runtime_json.value("health_endpoint", std::string());
        card.machine_spec.runtime.metadata_endpoint =
            runtime_json.value("metadata_endpoint", std::string());
        card.machine_spec.runtime.timeout_ms = runtime_json.value("timeout_ms", 0);

        if (machine_spec_json.contains("tokenizer")) {
            const json& tokenizer_json = machine_spec_json.at("tokenizer");
            if (!tokenizer_json.is_object()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "machine_spec.tokenizer must be an object.");
            }
            TokenizerSpec tokenizer;
            tokenizer.type = tokenizer_json.value("type", std::string());
            tokenizer.tokenizer_uri =
                tokenizer_json.value("tokenizer_uri", std::string());
            tokenizer.max_length = ReadOptional<int>(tokenizer_json, "max_length");
            card.machine_spec.tokenizer = std::move(tokenizer);
        }

        if (machine_spec_json.contains("preprocess")) {
            const json& preprocess_json = machine_spec_json.at("preprocess");
            if (!preprocess_json.is_object()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "machine_spec.preprocess must be an object.");
            }
            ProcessSpec preprocess;
            preprocess.config_uri =
                preprocess_json.value("config_uri", std::string());
            preprocess.label_map_uri =
                preprocess_json.value("label_map_uri", std::string());
            card.machine_spec.preprocess = std::move(preprocess);
        }

        if (machine_spec_json.contains("postprocess")) {
            const json& postprocess_json = machine_spec_json.at("postprocess");
            if (!postprocess_json.is_object()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "machine_spec.postprocess must be an object.");
            }
            ProcessSpec postprocess;
            postprocess.config_uri =
                postprocess_json.value("config_uri", std::string());
            postprocess.label_map_uri =
                postprocess_json.value("label_map_uri", std::string());
            card.machine_spec.postprocess = std::move(postprocess);
        }

        if (json_value.contains("constraints")) {
            const json& constraints_json = json_value.at("constraints");
            if (!constraints_json.is_object()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "constraints must be an object.");
            }
            ConstraintsSpec constraints;
            constraints.max_input_chars = ReadOptional<int>(constraints_json, "max_input_chars");
            constraints.max_request_bytes =
                ReadOptional<int>(constraints_json, "max_request_bytes");
            constraints.batch_supported =
                ReadOptional<bool>(constraints_json, "batch_supported");
            constraints.streaming_supported =
                ReadOptional<bool>(constraints_json, "streaming_supported");
            card.constraints = std::move(constraints);
        }

        if (json_value.contains("performance")) {
            const json& performance_json = json_value.at("performance");
            if (!performance_json.is_object()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "performance must be an object.");
            }
            PerformanceSpec performance;
            performance.latency_ms_p50 =
                ReadOptional<int>(performance_json, "latency_ms_p50");
            performance.latency_ms_p95 =
                ReadOptional<int>(performance_json, "latency_ms_p95");
            performance.primary_metric =
                performance_json.value("primary_metric", std::string());
            performance.primary_score = ReadOptional<double>(performance_json, "primary_score");
            performance.time_complexity =
                performance_json.value("time_complexity", std::string());
            performance.space_complexity =
                performance_json.value("space_complexity", std::string());
            performance.complexity_variable =
                performance_json.value("complexity_variable", std::string());
            performance.performance_notes =
                performance_json.value("performance_notes", std::string());
            card.performance = std::move(performance);
        }

        if (json_value.contains("resource_requirements")) {
            const json& resource_json = json_value.at("resource_requirements");
            if (!resource_json.is_object()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "resource_requirements must be an object.");
            }
            ResourceRequirementsSpec resource_requirements;
            resource_requirements.min_cpu_cores =
                ReadOptional<int>(resource_json, "min_cpu_cores");
            resource_requirements.recommended_cpu_cores =
                ReadOptional<int>(resource_json, "recommended_cpu_cores");
            resource_requirements.min_memory_mb =
                ReadOptional<int>(resource_json, "min_memory_mb");
            resource_requirements.recommended_memory_mb =
                ReadOptional<int>(resource_json, "recommended_memory_mb");
            resource_requirements.min_gpu_count =
                ReadOptional<int>(resource_json, "min_gpu_count");
            resource_requirements.gpu_type =
                resource_json.value("gpu_type", std::string());
            resource_requirements.min_vram_mb =
                ReadOptional<int>(resource_json, "min_vram_mb");
            resource_requirements.recommended_vram_mb =
                ReadOptional<int>(resource_json, "recommended_vram_mb");
            resource_requirements.disk_mb = ReadOptional<int>(resource_json, "disk_mb");
            card.resource_requirements = std::move(resource_requirements);
        }

        if (json_value.contains("model_profile")) {
            const json& model_profile_json = json_value.at("model_profile");
            if (!model_profile_json.is_object()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "model_profile must be an object.");
            }
            ModelProfileSpec model_profile;
            model_profile.parameter_count =
                ReadOptional<long long>(model_profile_json, "parameter_count");
            model_profile.parameter_count_text =
                model_profile_json.value("parameter_count_text", std::string());
            model_profile.flops = ReadOptional<long long>(model_profile_json, "flops");
            model_profile.flops_text =
                model_profile_json.value("flops_text", std::string());
            model_profile.flops_input_shape =
                model_profile_json.value("flops_input_shape", std::vector<int>{});
            model_profile.model_size_mb =
                ReadOptional<int>(model_profile_json, "model_size_mb");
            model_profile.precision =
                model_profile_json.value("precision", std::string());
            card.model_profile = std::move(model_profile);
        }

        if (json_value.contains("safety")) {
            const json& safety_json = json_value.at("safety");
            if (!safety_json.is_object()) {
                return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                     "safety must be an object.");
            }
            SafetySpec safety;
            safety.risk_level = safety_json.value("risk_level", std::string());
            safety.requires_human_review =
                ReadOptional<bool>(safety_json, "requires_human_review");
            card.safety = std::move(safety);
        }

        return card;
    } catch (const std::exception& ex) {
        return Status::Error(ErrorCode::kInvalidAlgorithmCard, ex.what());
    }
}

std::string ToYamlString(const AlgorithmCard& card) {
    YAML::Node root = JsonToYamlNode(ToJson(card));
    YAML::Emitter emitter;
    emitter << root;
    return emitter.c_str();
}

}  // namespace algolib
