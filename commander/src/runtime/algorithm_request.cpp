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
    nlohmann::json j{
        {"request_id",   request.request_id},
        {"trace_id",     request.trace_id},
        {"algorithm_id", request.algorithm_id},
        {"version",      request.version},
        {"backend_type", ToString(request.backend_type)},
        {"inputs",       request.inputs},
        {"params",       request.params},
    };
    if (!request.deploy_id.empty()) {
        j["deploy_id"] = request.deploy_id;
    }
    if (request.function_context.has_value()) {
        j["function_context"] = {
            {"function_id", request.function_context->function_id},
            {"function_code", request.function_context->function_code},
            {"workflow_instance_id", request.function_context->workflow_instance_id},
            {"step_instance_id", request.function_context->step_instance_id},
        };
    }
    return j;
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
    if (json_value.contains("function_context")) {
        const auto& context = json_value.at("function_context");
        if (!context.is_object()) {
            return Status::Error(
                ErrorCode::kInvalidArgument,
                "AlgorithmRequest function_context must be an object when provided.");
        }
        for (const std::string& field_name : {"function_id", "function_code",
                                               "workflow_instance_id", "step_instance_id"}) {
            if (context.contains(field_name) && !context.at(field_name).is_string()) {
                return Status::Error(
                    ErrorCode::kInvalidArgument,
                    "AlgorithmRequest function_context." + field_name +
                        " must be a string when provided.");
            }
        }
        if (context.value("function_id", std::string()).empty() &&
            context.value("function_code", std::string()).empty()) {
            return Status::Error(
                ErrorCode::kInvalidArgument,
                "AlgorithmRequest function_context must contain function_id or function_code.");
        }
    }

    AlgorithmRequest request;
    request.request_id = json_value.value("request_id", std::string());
    request.trace_id = json_value.value("trace_id", std::string());
    request.algorithm_id = json_value.at("algorithm_id").get<std::string>();
    request.version = json_value.at("version").get<std::string>();
    request.backend_type = backend_result.value();
    request.inputs = json_value.at("inputs");
    request.params     = json_value.value("params",     nlohmann::json::object());
    request.deploy_id   = json_value.value("deploy_id",  std::string());
    if (json_value.contains("function_context")) {
        const auto& context_json = json_value.at("function_context");
        FunctionContext context;
        context.function_id = context_json.value("function_id", std::string());
        context.function_code = context_json.value("function_code", std::string());
        context.workflow_instance_id =
            context_json.value("workflow_instance_id", std::string());
        context.step_instance_id =
            context_json.value("step_instance_id", std::string());
        request.function_context = std::move(context);
    }
    return request;
}

}  // namespace algolib
