#include "algolib/runtime/algorithm_request.h"

#include <string>

namespace algolib {
namespace {

Status RequireStringField(const nlohmann::json& json_value,
                          const std::string& field_name) {
    if (!json_value.contains(field_name) || !json_value.at(field_name).is_string() ||
        json_value.at(field_name).get<std::string>().empty()) {
        return Status::Error(
            ErrorCode::kInvalidArgument,
            "AlgorithmRequest must contain non-empty string field " + field_name + ".");
    }
    return Status::Ok();
}

}  // namespace

nlohmann::json ToJson(const AlgorithmRequest& request) {
    return nlohmann::json{
        {"request_id", request.request_id},
        {"trace_id", request.trace_id},
        {"algorithm_id", request.algorithm_id},
        {"version", request.version},
        {"backend_type", ToString(request.backend_type)},
        {"inputs", request.inputs},
        {"params", request.params},
    };
}

Result<AlgorithmRequest> AlgorithmRequestFromJson(const nlohmann::json& json_value) {
    if (!json_value.is_object()) {
        return Status::Error(
            ErrorCode::kInvalidArgument,
            "AlgorithmRequest JSON must be an object.");
    }

    for (const std::string& field_name : {"algorithm_id", "version", "backend_type"}) {
        auto field_status = RequireStringField(json_value, field_name);
        if (!field_status.ok()) {
            return field_status;
        }
    }

    if (!json_value.contains("inputs")) {
        return Status::Error(
            ErrorCode::kInvalidArgument,
            "AlgorithmRequest must contain inputs.");
    }

    auto backend_result = ParseBackendType(json_value.at("backend_type").get<std::string>());
    if (!backend_result.ok()) {
        return backend_result.status();
    }

    if (json_value.contains("request_id") && !json_value.at("request_id").is_string()) {
        return Status::Error(
            ErrorCode::kInvalidArgument,
            "AlgorithmRequest request_id must be a string when provided.");
    }
    if (json_value.contains("trace_id") && !json_value.at("trace_id").is_string()) {
        return Status::Error(
            ErrorCode::kInvalidArgument,
            "AlgorithmRequest trace_id must be a string when provided.");
    }
    if (json_value.contains("params") && !json_value.at("params").is_object()) {
        return Status::Error(
            ErrorCode::kInvalidArgument,
            "AlgorithmRequest params must be a JSON object when provided.");
    }

    AlgorithmRequest request;
    request.request_id = json_value.value("request_id", std::string());
    request.trace_id = json_value.value("trace_id", std::string());
    request.algorithm_id = json_value.at("algorithm_id").get<std::string>();
    request.version = json_value.at("version").get<std::string>();
    request.backend_type = backend_result.value();
    request.inputs = json_value.at("inputs");
    request.params = json_value.value("params", nlohmann::json::object());
    return request;
}

}  // namespace algolib
