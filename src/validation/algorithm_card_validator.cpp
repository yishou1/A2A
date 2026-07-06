#include "algolib/validation/algorithm_card_validator.h"

#include <algorithm>
#include <utility>
#include <vector>

#include "algolib/io/file_utils.h"
#include "algolib/io/json_utils.h"
#include "algolib/io/yaml_utils.h"
#include "algolib/core/schema_validator.h"
#include "algolib/validation/onnx_package_validator.h"
#include "algolib/validation/python_service_validator.h"

namespace algolib {
namespace {

using nlohmann::json;

Status RequireNonEmpty(const std::string& value, const std::string& field_name) {
    if (value.empty()) {
        return Status::Error(ErrorCode::kMissingRequiredField,
                             "Missing required field: " + field_name + ".");
    }
    return Status::Ok();
}

Status RequireNotEmpty(const std::vector<std::string>& values,
                       const std::string& field_name) {
    if (values.empty()) {
        return Status::Error(ErrorCode::kMissingRequiredField,
                             "Missing required field: " + field_name + ".");
    }
    return Status::Ok();
}

Status RequireFileExists(const std::filesystem::path& file_path,
                         ErrorCode error_code,
                         const std::string& field_name) {
    if (!std::filesystem::exists(file_path)) {
        return Status::Error(
            error_code,
            "Required file for " + field_name + " was not found: " + file_path.generic_string());
    }
    return Status::Ok();
}

Result<std::vector<std::string>> ReadStringList(const YAML::Node& node,
                                                const std::string& field_name) {
    if (!node || !node.IsSequence()) {
        return Status::Error(ErrorCode::kMissingRequiredField,
                             "Field " + field_name + " must be a non-empty array.");
    }

    std::vector<std::string> values;
    for (const auto& item : node) {
        if (!item.IsScalar()) {
            return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                                 "Field " + field_name + " must contain scalar strings.");
        }
        values.push_back(item.as<std::string>());
    }
    return values;
}

Status AppendStatus(Status primary_status, const Status& secondary_status) {
    if (!primary_status.ok()) {
        return primary_status;
    }
    return secondary_status;
}

Result<AlgorithmCard> ParseAlgorithmCard(const YAML::Node& root) {
    if (!root || !root.IsMap()) {
        return Status::Error(ErrorCode::kInvalidAlgorithmCard,
                             "algorithm_card.yaml root must be a map.");
    }

    if (!root["backend_type"]) {
        return Status::Error(ErrorCode::kMissingRequiredField,
                             "Missing required field: backend_type.");
    }
    if (!root["status"]) {
        return Status::Error(ErrorCode::kMissingRequiredField,
                             "Missing required field: status.");
    }
    if (!root["machine_spec"] || !root["machine_spec"].IsMap()) {
        return Status::Error(ErrorCode::kMissingRequiredField,
                             "Missing required field: machine_spec.");
    }

    AlgorithmCard card;

    if (root["algorithm_id"]) {
        card.algorithm_id = root["algorithm_id"].as<std::string>();
    }
    if (root["version"]) {
        card.version = root["version"].as<std::string>();
    }
    if (root["display_name"]) {
        card.display_name = root["display_name"].as<std::string>();
    }
    if (root["task_family"]) {
        card.task_family = root["task_family"].as<std::string>();
    }

    if (root["backend_type"]) {
        auto backend_result = ParseBackendType(root["backend_type"].as<std::string>());
        if (!backend_result.ok()) {
            return backend_result.status();
        }
        card.backend_type = backend_result.value();
    }

    if (root["status"]) {
        auto status_result = ParseAlgorithmStatus(root["status"].as<std::string>());
        if (!status_result.ok()) {
            return status_result.status();
        }
        card.status = status_result.value();
    }

    if (root["modalities"] && root["modalities"].IsMap()) {
        auto input_result = ReadStringList(root["modalities"]["input"], "modalities.input");
        if (!input_result.ok()) {
            return input_result.status();
        }
        card.modalities.input = input_result.value();

        auto output_result = ReadStringList(root["modalities"]["output"], "modalities.output");
        if (!output_result.ok()) {
            return output_result.status();
        }
        card.modalities.output = output_result.value();
    }

    if (root["capabilities"]) {
        auto capabilities_result = ReadStringList(root["capabilities"], "capabilities");
        if (!capabilities_result.ok()) {
            return capabilities_result.status();
        }
        card.capabilities = capabilities_result.value();
    }

    if (root["agent_card"] && root["agent_card"].IsMap()) {
        const YAML::Node& agent_card_node = root["agent_card"];
        if (agent_card_node["summary"]) {
            card.agent_card.summary = agent_card_node["summary"].as<std::string>();
        }
        if (agent_card_node["when_to_use"]) {
            auto use_result = ReadStringList(agent_card_node["when_to_use"],
                                             "agent_card.when_to_use");
            if (!use_result.ok()) {
                return use_result.status();
            }
            card.agent_card.when_to_use = use_result.value();
        }
        if (agent_card_node["when_not_to_use"]) {
            auto not_use_result =
                ReadStringList(agent_card_node["when_not_to_use"],
                               "agent_card.when_not_to_use");
            if (!not_use_result.ok()) {
                return not_use_result.status();
            }
            card.agent_card.when_not_to_use = not_use_result.value();
        }
        if (agent_card_node["input_description"]) {
            card.agent_card.input_description =
                agent_card_node["input_description"].as<std::string>();
        }
        if (agent_card_node["output_description"]) {
            card.agent_card.output_description =
                agent_card_node["output_description"].as<std::string>();
        }
        if (agent_card_node["examples"] && agent_card_node["examples"].IsSequence()) {
            for (const auto& example_node : agent_card_node["examples"]) {
                AgentCardExample example;
                if (example_node["input"]) {
                    example.input = YamlUtils::YamlNodeToJson(example_node["input"]);
                }
                if (example_node["output"]) {
                    example.output = YamlUtils::YamlNodeToJson(example_node["output"]);
                }
                card.agent_card.examples.push_back(std::move(example));
            }
        }
    }

    if (root["machine_spec"] && root["machine_spec"].IsMap()) {
        const YAML::Node& machine_spec_node = root["machine_spec"];
        if (machine_spec_node["input_schema_ref"]) {
            card.machine_spec.input_schema_ref =
                machine_spec_node["input_schema_ref"].as<std::string>();
        }
        if (machine_spec_node["output_schema_ref"]) {
            card.machine_spec.output_schema_ref =
                machine_spec_node["output_schema_ref"].as<std::string>();
        }
        if (machine_spec_node["tensor_contract_ref"]) {
            card.machine_spec.tensor_contract_ref =
                machine_spec_node["tensor_contract_ref"].as<std::string>();
        }
        if (machine_spec_node["runtime"] && machine_spec_node["runtime"].IsMap()) {
            const YAML::Node& runtime_node = machine_spec_node["runtime"];
            if (!runtime_node["backend_type"]) {
                return Status::Error(
                    ErrorCode::kMissingRequiredField,
                    "Missing required field: machine_spec.runtime.backend_type.");
            }
            if (runtime_node["backend_type"]) {
                auto runtime_backend_result =
                    ParseBackendType(runtime_node["backend_type"].as<std::string>());
                if (!runtime_backend_result.ok()) {
                    return runtime_backend_result.status();
                }
                card.machine_spec.runtime.backend_type = runtime_backend_result.value();
            }
            if (runtime_node["model_uri"]) {
                card.machine_spec.runtime.model_uri = runtime_node["model_uri"].as<std::string>();
            }
            if (runtime_node["execution_provider"]) {
                card.machine_spec.runtime.execution_provider =
                    runtime_node["execution_provider"].as<std::string>();
            }
            if (runtime_node["endpoint"]) {
                card.machine_spec.runtime.endpoint = runtime_node["endpoint"].as<std::string>();
            }
            if (runtime_node["health_endpoint"]) {
                card.machine_spec.runtime.health_endpoint =
                    runtime_node["health_endpoint"].as<std::string>();
            }
            if (runtime_node["metadata_endpoint"]) {
                card.machine_spec.runtime.metadata_endpoint =
                    runtime_node["metadata_endpoint"].as<std::string>();
            }
            if (runtime_node["timeout_ms"]) {
                card.machine_spec.runtime.timeout_ms = runtime_node["timeout_ms"].as<int>();
            }
        }

        if (machine_spec_node["tokenizer"] && machine_spec_node["tokenizer"].IsMap()) {
            TokenizerSpec tokenizer;
            if (machine_spec_node["tokenizer"]["type"]) {
                tokenizer.type = machine_spec_node["tokenizer"]["type"].as<std::string>();
            }
            if (machine_spec_node["tokenizer"]["tokenizer_uri"]) {
                tokenizer.tokenizer_uri =
                    machine_spec_node["tokenizer"]["tokenizer_uri"].as<std::string>();
            }
            if (machine_spec_node["tokenizer"]["max_length"]) {
                tokenizer.max_length =
                    machine_spec_node["tokenizer"]["max_length"].as<int>();
            }
            card.machine_spec.tokenizer = std::move(tokenizer);
        }

        if (machine_spec_node["preprocess"] && machine_spec_node["preprocess"].IsMap()) {
            ProcessSpec preprocess;
            if (machine_spec_node["preprocess"]["config_uri"]) {
                preprocess.config_uri =
                    machine_spec_node["preprocess"]["config_uri"].as<std::string>();
            }
            if (machine_spec_node["preprocess"]["label_map_uri"]) {
                preprocess.label_map_uri =
                    machine_spec_node["preprocess"]["label_map_uri"].as<std::string>();
            }
            card.machine_spec.preprocess = std::move(preprocess);
        }

        if (machine_spec_node["postprocess"] && machine_spec_node["postprocess"].IsMap()) {
            ProcessSpec postprocess;
            if (machine_spec_node["postprocess"]["config_uri"]) {
                postprocess.config_uri =
                    machine_spec_node["postprocess"]["config_uri"].as<std::string>();
            }
            if (machine_spec_node["postprocess"]["label_map_uri"]) {
                postprocess.label_map_uri =
                    machine_spec_node["postprocess"]["label_map_uri"].as<std::string>();
            }
            card.machine_spec.postprocess = std::move(postprocess);
        }
    }

    if (root["constraints"] && root["constraints"].IsMap()) {
        ConstraintsSpec constraints;
        if (root["constraints"]["max_input_chars"]) {
            constraints.max_input_chars = root["constraints"]["max_input_chars"].as<int>();
        }
        if (root["constraints"]["max_request_bytes"]) {
            constraints.max_request_bytes = root["constraints"]["max_request_bytes"].as<int>();
        }
        if (root["constraints"]["batch_supported"]) {
            constraints.batch_supported = root["constraints"]["batch_supported"].as<bool>();
        }
        if (root["constraints"]["streaming_supported"]) {
            constraints.streaming_supported =
                root["constraints"]["streaming_supported"].as<bool>();
        }
        card.constraints = std::move(constraints);
    }

    if (root["performance"] && root["performance"].IsMap()) {
        PerformanceSpec performance;
        if (root["performance"]["latency_ms_p50"]) {
            performance.latency_ms_p50 = root["performance"]["latency_ms_p50"].as<int>();
        }
        if (root["performance"]["latency_ms_p95"]) {
            performance.latency_ms_p95 = root["performance"]["latency_ms_p95"].as<int>();
        }
        if (root["performance"]["primary_metric"]) {
            performance.primary_metric = root["performance"]["primary_metric"].as<std::string>();
        }
        if (root["performance"]["primary_score"]) {
            performance.primary_score = root["performance"]["primary_score"].as<double>();
        }
        if (root["performance"]["time_complexity"]) {
            performance.time_complexity =
                root["performance"]["time_complexity"].as<std::string>();
        }
        if (root["performance"]["space_complexity"]) {
            performance.space_complexity =
                root["performance"]["space_complexity"].as<std::string>();
        }
        if (root["performance"]["complexity_variable"]) {
            performance.complexity_variable =
                root["performance"]["complexity_variable"].as<std::string>();
        }
        if (root["performance"]["performance_notes"]) {
            performance.performance_notes =
                root["performance"]["performance_notes"].as<std::string>();
        }
        card.performance = std::move(performance);
    }

    if (root["safety"] && root["safety"].IsMap()) {
        SafetySpec safety;
        if (root["safety"]["risk_level"]) {
            safety.risk_level = root["safety"]["risk_level"].as<std::string>();
        }
        if (root["safety"]["requires_human_review"]) {
            safety.requires_human_review =
                root["safety"]["requires_human_review"].as<bool>();
        }
        card.safety = std::move(safety);
    }

    return card;
}

Status ValidateCommonFields(const AlgorithmCard& card) {
    Status status = RequireNonEmpty(card.algorithm_id, "algorithm_id");
    status = AppendStatus(std::move(status), RequireNonEmpty(card.version, "version"));
    status = AppendStatus(std::move(status), RequireNonEmpty(card.display_name, "display_name"));
    status = AppendStatus(std::move(status), RequireNonEmpty(card.task_family, "task_family"));
    status = AppendStatus(std::move(status), RequireNotEmpty(card.modalities.input, "modalities.input"));
    status = AppendStatus(std::move(status), RequireNotEmpty(card.modalities.output, "modalities.output"));
    status = AppendStatus(std::move(status), RequireNotEmpty(card.capabilities, "capabilities"));
    status = AppendStatus(std::move(status), RequireNonEmpty(card.agent_card.summary, "agent_card.summary"));
    status = AppendStatus(std::move(status),
                          RequireNotEmpty(card.agent_card.when_to_use,
                                          "agent_card.when_to_use"));
    status = AppendStatus(std::move(status),
                          RequireNotEmpty(card.agent_card.when_not_to_use,
                                          "agent_card.when_not_to_use"));
    status = AppendStatus(std::move(status),
                          RequireNonEmpty(card.agent_card.input_description,
                                          "agent_card.input_description"));
    status = AppendStatus(std::move(status),
                          RequireNonEmpty(card.agent_card.output_description,
                                          "agent_card.output_description"));
    status = AppendStatus(std::move(status),
                          RequireNonEmpty(card.machine_spec.input_schema_ref,
                                          "machine_spec.input_schema_ref"));
    status = AppendStatus(std::move(status),
                          RequireNonEmpty(card.machine_spec.output_schema_ref,
                                          "machine_spec.output_schema_ref"));

    if (!status.ok()) {
        return status;
    }

    if (card.machine_spec.runtime.backend_type != card.backend_type) {
        return Status::Error(
            ErrorCode::kBackendTypeMismatch,
            "machine_spec.runtime.backend_type does not match top-level backend_type.");
    }
    return Status::Ok();
}

Status ValidateBackendSpecificFields(const AlgorithmCard& card,
                                     const std::filesystem::path& package_root) {
    switch (card.backend_type) {
        case BackendType::kOnnx: {
            Status status = RequireNonEmpty(card.machine_spec.runtime.model_uri,
                                            "machine_spec.runtime.model_uri");
            if (!card.machine_spec.preprocess.has_value()) {
                return Status::Error(ErrorCode::kMissingRequiredField,
                                     "machine_spec.preprocess is required for onnx.");
            }
            if (!card.machine_spec.postprocess.has_value()) {
                return Status::Error(ErrorCode::kMissingRequiredField,
                                     "machine_spec.postprocess is required for onnx.");
            }
            status = AppendStatus(std::move(status),
                                  RequireNonEmpty(card.machine_spec.preprocess->config_uri,
                                                  "machine_spec.preprocess.config_uri"));
            status = AppendStatus(std::move(status),
                                  RequireNonEmpty(card.machine_spec.postprocess->config_uri,
                                                  "machine_spec.postprocess.config_uri"));
            if (!status.ok()) {
                return status;
            }

            status = RequireFileExists(
                FileUtils::ResolveReference(package_root, card.machine_spec.runtime.model_uri),
                ErrorCode::kOnnxModelNotFound, "machine_spec.runtime.model_uri");
            status = AppendStatus(
                std::move(status),
                RequireFileExists(FileUtils::ResolveReference(package_root,
                                                              card.machine_spec.input_schema_ref),
                                  ErrorCode::kInputSchemaInvalid,
                                  "machine_spec.input_schema_ref"));
            status = AppendStatus(
                std::move(status),
                RequireFileExists(FileUtils::ResolveReference(package_root,
                                                              card.machine_spec.output_schema_ref),
                                  ErrorCode::kOutputSchemaInvalid,
                                  "machine_spec.output_schema_ref"));
            status = AppendStatus(
                std::move(status),
                RequireFileExists(FileUtils::ResolveReference(package_root,
                                                              card.machine_spec.preprocess->config_uri),
                                  ErrorCode::kPreprocessFailed,
                                  "machine_spec.preprocess.config_uri"));
            status = AppendStatus(
                std::move(status),
                RequireFileExists(FileUtils::ResolveReference(package_root,
                                                              card.machine_spec.postprocess->config_uri),
                                  ErrorCode::kPostprocessFailed,
                                  "machine_spec.postprocess.config_uri"));
            if (!card.machine_spec.tensor_contract_ref.empty()) {
                status = AppendStatus(
                    std::move(status),
                    RequireFileExists(FileUtils::ResolveReference(
                                          package_root,
                                          card.machine_spec.tensor_contract_ref),
                                      ErrorCode::kInvalidAlgorithmCard,
                                      "machine_spec.tensor_contract_ref"));
            }
            if (card.machine_spec.tokenizer.has_value() &&
                !card.machine_spec.tokenizer->tokenizer_uri.empty()) {
                status = AppendStatus(
                    std::move(status),
                    RequireFileExists(FileUtils::ResolveReference(
                                          package_root,
                                          card.machine_spec.tokenizer->tokenizer_uri),
                                      ErrorCode::kTokenizerNotSupported,
                                      "machine_spec.tokenizer.tokenizer_uri"));
            }
            if (card.machine_spec.postprocess.has_value() &&
                !card.machine_spec.postprocess->label_map_uri.empty()) {
                status = AppendStatus(
                    std::move(status),
                    RequireFileExists(FileUtils::ResolveReference(
                                          package_root,
                                          card.machine_spec.postprocess->label_map_uri),
                                      ErrorCode::kPostprocessFailed,
                                      "machine_spec.postprocess.label_map_uri"));
            }
            return status;
        }
        case BackendType::kPythonHttpService: {
            Status status = RequireNonEmpty(card.machine_spec.runtime.endpoint,
                                            "machine_spec.runtime.endpoint");
            status = AppendStatus(std::move(status),
                                  RequireNonEmpty(card.machine_spec.runtime.health_endpoint,
                                                  "machine_spec.runtime.health_endpoint"));
            status = AppendStatus(std::move(status),
                                  RequireNonEmpty(card.machine_spec.runtime.metadata_endpoint,
                                                  "machine_spec.runtime.metadata_endpoint"));
            if (card.machine_spec.runtime.timeout_ms <= 0) {
                return Status::Error(ErrorCode::kMissingRequiredField,
                                     "machine_spec.runtime.timeout_ms must be greater than 0.");
            }

            status = AppendStatus(
                std::move(status),
                RequireFileExists(FileUtils::ResolveReference(package_root,
                                                              card.machine_spec.input_schema_ref),
                                  ErrorCode::kInputSchemaInvalid,
                                  "machine_spec.input_schema_ref"));
            status = AppendStatus(
                std::move(status),
                RequireFileExists(FileUtils::ResolveReference(package_root,
                                                              card.machine_spec.output_schema_ref),
                                  ErrorCode::kOutputSchemaInvalid,
                                  "machine_spec.output_schema_ref"));
            return status;
        }
    }

    return Status::Error(ErrorCode::kUnsupportedBackendType,
                         "Unsupported backend_type in algorithm card.");
}

}  // namespace

Result<ValidatedAlgorithmPackage> AlgorithmCardValidator::ValidateFromPath(
    const std::filesystem::path& package_or_card_path) const {
    auto card_path_result = FileUtils::ResolveCardPath(package_or_card_path);
    if (!card_path_result.ok()) {
        return card_path_result.status();
    }

    const std::filesystem::path card_path = card_path_result.value();
    const std::filesystem::path package_root = card_path.parent_path();

    auto yaml_result = YamlUtils::LoadYamlFile(card_path);
    if (!yaml_result.ok()) {
        return yaml_result.status();
    }

    auto card_result = ParseAlgorithmCard(yaml_result.value());
    if (!card_result.ok()) {
        return card_result.status();
    }

    const AlgorithmCard& card = card_result.value();
    auto common_status = ValidateCommonFields(card);
    if (!common_status.ok()) {
        return common_status;
    }

    auto backend_status = ValidateBackendSpecificFields(card, package_root);
    if (!backend_status.ok()) {
        return backend_status;
    }

    SchemaValidator schema_validator;
    // 中文注释：Phase 2 开始，注册阶段就要求 Schema 文件既能加载，也必须属于支持的子集。
    auto input_schema_result = schema_validator.LoadSchema(
        FileUtils::ResolveReference(package_root, card.machine_spec.input_schema_ref),
        ErrorCode::kInputSchemaInvalid);
    if (!input_schema_result.ok()) {
        return input_schema_result.status();
    }

    auto output_schema_result = schema_validator.LoadSchema(
        FileUtils::ResolveReference(package_root, card.machine_spec.output_schema_ref),
        ErrorCode::kOutputSchemaInvalid);
    if (!output_schema_result.ok()) {
        return output_schema_result.status();
    }

    if (card.backend_type == BackendType::kOnnx) {
        // 中文注释：Phase 4 起，ONNX 注册不仅检查文件存在，还要执行 package 级联调校验。
        OnnxPackageValidator onnx_package_validator;
        auto onnx_status = onnx_package_validator.ValidatePackage(package_root, card);
        if (!onnx_status.ok()) {
            return onnx_status;
        }
    }

    if (card.backend_type == BackendType::kPythonHttpService) {
        // 中文注释：Phase 3 要求 python_http_service 在注册和 validate 阶段完成远端联调校验。
        PythonServiceValidator python_service_validator;
        auto service_status = python_service_validator.ValidateService(package_root, card);
        if (!service_status.ok()) {
            return service_status;
        }
    }

    ValidatedAlgorithmPackage package;
    package.package_root = package_root;
    package.card_path = card_path;
    package.card = card;
    package.input_schema_summary = JsonUtils::SummarizeSchema(input_schema_result.value());
    package.output_schema_summary = JsonUtils::SummarizeSchema(output_schema_result.value());
    return package;
}

}  // namespace algolib
