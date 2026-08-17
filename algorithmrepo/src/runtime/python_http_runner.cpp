#include "algolib/runtime/python_http_runner.h"

#include <chrono>
#include <string>
#include <thread>

#include "algolib/runtime/algorithm_request.h"

namespace algolib {
namespace {

Status EnsureHttpSuccess(const HttpResponse& response,
                         const std::string& endpoint_name,
                         const std::string& url) {
    if (response.status_code == 200) {
        return Status::Ok();
    }
    if (response.status_code == 503) {
        return Status::Error(
            ErrorCode::kServiceNotReady,
            endpoint_name + " returned HTTP 503 for " + url + ".");
    }
    if (response.status_code == 504) {
        return Status::Error(
            ErrorCode::kServiceTimeout,
            endpoint_name + " returned HTTP 504 for " + url + ".");
    }
    return Status::Error(
        ErrorCode::kServiceHttpError,
        endpoint_name + " returned HTTP " + std::to_string(response.status_code) +
            " for " + url + ".");
}

constexpr int kRetryAttempts = 4;
constexpr auto kRetryDelay = std::chrono::milliseconds(75);

bool IsRetryableStatusCode(ErrorCode code) {
    return code == ErrorCode::kServiceUnavailable ||
           code == ErrorCode::kServiceTimeout ||
           code == ErrorCode::kServiceNotReady;
}

Result<nlohmann::json> ParseResponseJson(const HttpResponse& response,
                                         const std::string& endpoint_name) {
    try {
        return nlohmann::json::parse(response.body);
    } catch (const std::exception& ex) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            endpoint_name + " returned invalid JSON: " + ex.what());
    }
}

Status RequireObject(const nlohmann::json& payload, const std::string& context) {
    if (!payload.is_object()) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            context + " must be a JSON object.");
    }
    return Status::Ok();
}

AlgorithmResult BuildFailureResult(const AlgorithmRequest& request,
                                   const std::string& error_code,
                                   const std::string& message,
                                   const nlohmann::json& usage = nlohmann::json::object()) {
    AlgorithmResult result;
    result.ok = false;
    result.request_id = request.request_id;
    result.trace_id = request.trace_id;
    result.algorithm_id = request.algorithm_id;
    result.version = request.version;
    result.backend_type = request.backend_type;
    result.outputs = nlohmann::json::object();
    result.usage = usage.is_object() ? usage : nlohmann::json::object();
    result.error = AlgorithmError{error_code, message};
    return result;
}

AlgorithmResult BuildStatusFailureResult(const AlgorithmRequest& request,
                                         const Status& status) {
    return BuildFailureResult(request, ToString(status.code()), status.message());
}

Status ValidateHealthPayload(const nlohmann::json& payload,
                             const AlgorithmEntry& entry) {
    const auto object_status = RequireObject(payload, "/health response");
    if (!object_status.ok()) {
        return object_status;
    }

    if (!payload.contains("ok") || !payload.at("ok").is_boolean()) {
        return Status::Error(ErrorCode::kServiceResponseInvalid,
                             "/health response must contain boolean field ok.");
    }
    if (!payload.contains("status") || !payload.at("status").is_string()) {
        return Status::Error(ErrorCode::kServiceResponseInvalid,
                             "/health response must contain string field status.");
    }
    if (!payload.contains("algorithm_id") || !payload.at("algorithm_id").is_string()) {
        return Status::Error(ErrorCode::kServiceResponseInvalid,
                             "/health response must contain string field algorithm_id.");
    }
    if (!payload.contains("version") || !payload.at("version").is_string()) {
        return Status::Error(ErrorCode::kServiceResponseInvalid,
                             "/health response must contain string field version.");
    }
    if (!payload.contains("model_loaded") || !payload.at("model_loaded").is_boolean()) {
        return Status::Error(ErrorCode::kServiceResponseInvalid,
                             "/health response must contain boolean field model_loaded.");
    }

    const std::string status = payload.at("status").get<std::string>();
    if (payload.at("algorithm_id").get<std::string>() != entry.card.algorithm_id ||
        payload.at("version").get<std::string>() != entry.card.version) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            "/health algorithm identity does not match algorithm_card.yaml.");
    }

    if (!payload.at("ok").get<bool>() || status != "ready" ||
        !payload.at("model_loaded").get<bool>()) {
        return Status::Error(
            ErrorCode::kServiceNotReady,
            "Python service is not ready. status=" + status +
                ", model_loaded=" +
                std::string(payload.at("model_loaded").get<bool>() ? "true" : "false") +
                ".");
    }

    return Status::Ok();
}

template <typename Operation>
Status RetryUntilOk(Operation operation) {
    Status last_status = Status::Error(ErrorCode::kServiceUnavailable,
                                       "Python service retry did not execute.");
    for (int attempt = 0; attempt < kRetryAttempts; ++attempt) {
        last_status = operation();
        if (last_status.ok()) {
            return last_status;
        }
        if (!IsRetryableStatusCode(last_status.code()) || attempt + 1 >= kRetryAttempts) {
            return last_status;
        }
        std::this_thread::sleep_for(kRetryDelay);
    }
    return last_status;
}

Result<nlohmann::json> FetchHealthPayload(const HttpClient& http_client,
                                          const AlgorithmEntry& entry) {
    const int timeout_ms = entry.card.machine_spec.runtime.timeout_ms;
    nlohmann::json payload;
    auto status = RetryUntilOk([&]() {
        auto health_result =
            http_client.Get(entry.card.machine_spec.runtime.health_endpoint, timeout_ms);
        if (!health_result.ok()) {
            return health_result.status();
        }

        auto http_status = EnsureHttpSuccess(
            health_result.value(), "GET /health", entry.card.machine_spec.runtime.health_endpoint);
        if (!http_status.ok()) {
            return http_status;
        }

        auto payload_result = ParseResponseJson(health_result.value(), "GET /health");
        if (!payload_result.ok()) {
            return payload_result.status();
        }

        auto payload_status = ValidateHealthPayload(payload_result.value(), entry);
        if (!payload_status.ok()) {
            return payload_status;
        }

        payload = payload_result.value();
        return Status::Ok();
    });
    if (!status.ok()) {
        return status;
    }

    return payload;
}

Result<nlohmann::json> FetchPredictPayload(const HttpClient& http_client,
                                           const AlgorithmEntry& entry,
                                           const AlgorithmRequest& request) {
    const int timeout_ms = entry.card.machine_spec.runtime.timeout_ms;
    nlohmann::json payload;
    auto status = RetryUntilOk([&]() {
        auto predict_result = http_client.PostJson(
            entry.card.machine_spec.runtime.endpoint, ToJson(request), timeout_ms);
        if (!predict_result.ok()) {
            return predict_result.status();
        }

        auto predict_http_status = EnsureHttpSuccess(
            predict_result.value(), "POST /predict", entry.card.machine_spec.runtime.endpoint);
        if (!predict_http_status.ok()) {
            return predict_http_status;
        }

        auto response_json = ParseResponseJson(predict_result.value(), "POST /predict");
        if (!response_json.ok()) {
            return response_json.status();
        }

        const auto object_status = RequireObject(response_json.value(), "/predict response");
        if (!object_status.ok()) {
            return object_status;
        }

        payload = response_json.value();
        return Status::Ok();
    });
    if (!status.ok()) {
        return status;
    }
    return payload;
}

}  // namespace

Status PythonHttpRunner::Load(const AlgorithmEntry& entry) {
    if (entry.key.backend_type != BackendType::kPythonHttpService) {
        return Status::Error(
            ErrorCode::kBackendTypeMismatch,
            "PythonHttpRunner can only load python_http_service entries.");
    }

    entry_ = entry;
    loaded_ = true;
    return Status::Ok();
}

AlgorithmResult PythonHttpRunner::Run(const AlgorithmRequest& request) {
    if (!loaded_) {
        return BuildFailureResult(
            request,
            ToString(ErrorCode::kServiceUnavailable),
            "PythonHttpRunner must be loaded before Run().");
    }

    auto health_payload = FetchHealthPayload(http_client_, entry_);
    if (!health_payload.ok()) {
        return BuildStatusFailureResult(request, health_payload.status());
    }

    auto response_json = FetchPredictPayload(http_client_, entry_, request);
    if (!response_json.ok()) {
        return BuildStatusFailureResult(request, response_json.status());
    }

    const auto& payload = response_json.value();
    if (!payload.contains("ok") || !payload.at("ok").is_boolean()) {
        return BuildFailureResult(
            request,
            ToString(ErrorCode::kServiceResponseInvalid),
            "/predict response must contain boolean field ok.");
    }
    if (payload.value("algorithm_id", std::string()) != entry_.card.algorithm_id ||
        payload.value("version", std::string()) != entry_.card.version) {
        return BuildFailureResult(
            request,
            ToString(ErrorCode::kServiceResponseInvalid),
            "/predict response algorithm identity does not match algorithm_card.yaml.");
    }

    const nlohmann::json usage =
        payload.value("usage", nlohmann::json::object()).is_object()
            ? payload.value("usage", nlohmann::json::object())
            : nlohmann::json::object();

    if (!payload.at("ok").get<bool>()) {
        std::string error_code = ToString(ErrorCode::kServiceResponseInvalid);
        std::string error_message = "/predict returned ok=false.";
        if (payload.contains("error") && payload.at("error").is_object()) {
            const auto& error_json = payload.at("error");
            if (error_json.contains("code") && error_json.at("code").is_string()) {
                error_code = error_json.at("code").get<std::string>();
            }
            if (error_json.contains("message") && error_json.at("message").is_string()) {
                error_message = error_json.at("message").get<std::string>();
            }
        }

        AlgorithmResult result = BuildFailureResult(request, error_code, error_message, usage);
        result.outputs = payload.value("outputs", nlohmann::json::object());
        result.request_id = payload.value("request_id", request.request_id);
        result.trace_id = payload.value("trace_id", request.trace_id);
        return result;
    }

    if (!payload.contains("outputs")) {
        return BuildFailureResult(
            request,
            ToString(ErrorCode::kServiceResponseInvalid),
            "/predict response must contain outputs.",
            usage);
    }

    AlgorithmResult result;
    result.ok = true;
    result.request_id = payload.value("request_id", request.request_id);
    result.trace_id = payload.value("trace_id", request.trace_id);
    result.algorithm_id = request.algorithm_id;
    result.version = request.version;
    result.backend_type = request.backend_type;
    result.outputs = payload.at("outputs");
    result.usage = usage;
    result.error.reset();
    return result;
}

HealthStatus PythonHttpRunner::HealthCheck() const {
    if (!loaded_) {
        return HealthStatus{false, "unloaded", "PythonHttpRunner has not been loaded yet."};
    }

    auto health_payload = FetchHealthPayload(http_client_, entry_);
    if (!health_payload.ok()) {
        return HealthStatus{
            false,
            "error",
            health_payload.status().ToString(),
        };
    }

    return HealthStatus{true, "ready", ""};
}

}  // namespace algolib
