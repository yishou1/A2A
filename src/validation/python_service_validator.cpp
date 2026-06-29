#include "algolib/validation/python_service_validator.h"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/core/schema_validator.h"
#include "algolib/io/file_utils.h"
#include "algolib/io/http_client.h"
#include "algolib/io/json_utils.h"

namespace algolib {
namespace {

namespace fs = std::filesystem;
using nlohmann::json;

// 中文注释：Phase 3 的远端服务校验逻辑集中在这里，便于后续 runner 复用相同规则。
bool EndsWith(const std::string& value, const std::string& suffix) {
    return value.size() >= suffix.size() &&
           value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0;
}

constexpr int kRetryAttempts = 4;
constexpr auto kRetryDelay = std::chrono::milliseconds(75);

bool IsRetryableStatusCode(ErrorCode code) {
    return code == ErrorCode::kServiceUnavailable ||
           code == ErrorCode::kServiceTimeout ||
           code == ErrorCode::kServiceNotReady;
}

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

Result<json> ParseResponseJson(const HttpResponse& response,
                               const std::string& endpoint_name) {
    try {
        return json::parse(response.body);
    } catch (const std::exception& ex) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            endpoint_name + " returned invalid JSON: " + ex.what());
    }
}

Status RequireObject(const json& payload, const std::string& context) {
    if (!payload.is_object()) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            context + " must be a JSON object.");
    }
    return Status::Ok();
}

Status ValidateHealthPayload(const json& payload, const AlgorithmCard& card) {
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

    static const std::vector<std::string> kAllowedStatuses = {
        "starting",
        "loading",
        "ready",
        "degraded",
        "error",
    };

    const std::string status = payload.at("status").get<std::string>();
    if (std::find(kAllowedStatuses.begin(), kAllowedStatuses.end(), status) ==
        kAllowedStatuses.end()) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            "/health returned unsupported status: " + status + ".");
    }

    if (payload.at("algorithm_id").get<std::string>() != card.algorithm_id ||
        payload.at("version").get<std::string>() != card.version) {
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

Status ValidateMetadataPayload(const json& payload, const AlgorithmCard& card) {
    const auto object_status = RequireObject(payload, "/metadata response");
    if (!object_status.ok()) {
        return object_status;
    }

    if (!payload.contains("algorithm_id") || !payload.at("algorithm_id").is_string() ||
        !payload.contains("version") || !payload.at("version").is_string() ||
        !payload.contains("backend_type") || !payload.at("backend_type").is_string()) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            "/metadata response must contain string fields algorithm_id, version and backend_type.");
    }

    if (payload.at("algorithm_id").get<std::string>() != card.algorithm_id) {
        return Status::Error(
            ErrorCode::kServiceMetadataMismatch,
            "/metadata algorithm_id does not match algorithm_card.yaml.");
    }
    if (payload.at("version").get<std::string>() != card.version) {
        return Status::Error(
            ErrorCode::kServiceMetadataMismatch,
            "/metadata version does not match algorithm_card.yaml.");
    }
    if (payload.at("backend_type").get<std::string>() != "python_http_service") {
        return Status::Error(
            ErrorCode::kServiceMetadataMismatch,
            "/metadata backend_type must be python_http_service.");
    }

    return Status::Ok();
}

Result<fs::path> FindGoldenRequestPath(const fs::path& package_root) {
    const fs::path golden_dir = package_root / "golden_cases";
    if (!fs::exists(golden_dir) || !fs::is_directory(golden_dir)) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "golden_cases directory was not found: " + golden_dir.generic_string());
    }

    std::vector<fs::path> candidates;
    for (const auto& entry : fs::directory_iterator(golden_dir)) {
        if (!entry.is_regular_file()) {
            continue;
        }
        const std::string filename = entry.path().filename().generic_string();
        if (EndsWith(filename, "_request.json")) {
            candidates.push_back(entry.path());
        }
    }

    std::sort(candidates.begin(), candidates.end());
    if (candidates.empty()) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "No golden request JSON file was found under: " + golden_dir.generic_string());
    }
    return candidates.front();
}

Result<json> LoadGoldenRequest(const fs::path& package_root) {
    auto request_path_result = FindGoldenRequestPath(package_root);
    if (!request_path_result.ok()) {
        return request_path_result.status();
    }

    auto request_result = JsonUtils::ReadJsonFile(request_path_result.value());
    if (!request_result.ok()) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "Failed to load golden request file " +
                request_path_result.value().generic_string() + ": " +
                request_result.status().ToString());
    }
    return request_result.value();
}

Status ValidateGoldenRequest(const fs::path& package_root,
                             const AlgorithmCard& card,
                             const json& request_json) {
    const auto object_status = RequireObject(request_json, "golden request");
    if (!object_status.ok()) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "Golden request must be a JSON object.");
    }

    if (request_json.value("algorithm_id", std::string()) != card.algorithm_id ||
        request_json.value("version", std::string()) != card.version) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "Golden request algorithm identity does not match algorithm_card.yaml.");
    }
    if (!request_json.contains("inputs")) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "Golden request must contain inputs.");
    }

    SchemaValidator schema_validator;
    auto input_schema_result = schema_validator.LoadSchema(
        FileUtils::ResolveReference(package_root, card.machine_spec.input_schema_ref),
        ErrorCode::kInputSchemaInvalid);
    if (!input_schema_result.ok()) {
        return input_schema_result.status();
    }

    auto input_status = schema_validator.ValidateInstance(
        request_json.at("inputs"), input_schema_result.value(),
        ErrorCode::kInputSchemaInvalid, "$.inputs");
    if (!input_status.ok()) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "Golden request inputs do not satisfy input.schema.json: " +
                input_status.ToString());
    }

    return Status::Ok();
}

Status ValidatePredictPayload(const fs::path& package_root,
                              const AlgorithmCard& card,
                              const json& response_json) {
    const auto object_status = RequireObject(response_json, "/predict response");
    if (!object_status.ok()) {
        return object_status;
    }

    if (!response_json.contains("ok") || !response_json.at("ok").is_boolean()) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            "/predict response must contain boolean field ok.");
    }
    if (!response_json.at("ok").get<bool>()) {
        std::string error_message = "/predict returned ok=false.";
        if (response_json.contains("error") && response_json.at("error").is_object()) {
            const auto& error_json = response_json.at("error");
            if (error_json.contains("code") && error_json.at("code").is_string()) {
                error_message += " code=" + error_json.at("code").get<std::string>() + ".";
            }
            if (error_json.contains("message") && error_json.at("message").is_string()) {
                error_message += " message=" + error_json.at("message").get<std::string>() + ".";
            }
        }
        return Status::Error(ErrorCode::kServiceResponseInvalid, error_message);
    }

    if (response_json.value("algorithm_id", std::string()) != card.algorithm_id ||
        response_json.value("version", std::string()) != card.version) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            "/predict response algorithm identity does not match algorithm_card.yaml.");
    }
    if (!response_json.contains("outputs")) {
        return Status::Error(
            ErrorCode::kServiceResponseInvalid,
            "/predict response must contain outputs.");
    }

    SchemaValidator schema_validator;
    auto output_schema_result = schema_validator.LoadSchema(
        FileUtils::ResolveReference(package_root, card.machine_spec.output_schema_ref),
        ErrorCode::kServiceOutputSchemaInvalid);
    if (!output_schema_result.ok()) {
        return output_schema_result.status();
    }

    return schema_validator.ValidateInstance(
        response_json.at("outputs"), output_schema_result.value(),
        ErrorCode::kServiceOutputSchemaInvalid, "$.outputs");
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

Result<json> FetchHealthPayloadWithRetry(const HttpClient& http_client,
                                         const AlgorithmCard& card,
                                         int timeout_ms) {
    json payload;
    auto status = RetryUntilOk([&]() {
        auto health_result =
            http_client.Get(card.machine_spec.runtime.health_endpoint, timeout_ms);
        if (!health_result.ok()) {
            return health_result.status();
        }
        auto health_http_status = EnsureHttpSuccess(
            health_result.value(), "GET /health", card.machine_spec.runtime.health_endpoint);
        if (!health_http_status.ok()) {
            return health_http_status;
        }
        auto health_payload = ParseResponseJson(health_result.value(), "GET /health");
        if (!health_payload.ok()) {
            return health_payload.status();
        }
        auto payload_status = ValidateHealthPayload(health_payload.value(), card);
        if (!payload_status.ok()) {
            return payload_status;
        }
        payload = health_payload.value();
        return Status::Ok();
    });
    if (!status.ok()) {
        return status;
    }
    return payload;
}

Result<json> FetchMetadataPayloadWithRetry(const HttpClient& http_client,
                                           const AlgorithmCard& card,
                                           int timeout_ms) {
    json payload;
    auto status = RetryUntilOk([&]() {
        auto metadata_result =
            http_client.Get(card.machine_spec.runtime.metadata_endpoint, timeout_ms);
        if (!metadata_result.ok()) {
            return metadata_result.status();
        }
        auto metadata_http_status = EnsureHttpSuccess(
            metadata_result.value(), "GET /metadata", card.machine_spec.runtime.metadata_endpoint);
        if (!metadata_http_status.ok()) {
            return metadata_http_status;
        }
        auto metadata_payload = ParseResponseJson(metadata_result.value(), "GET /metadata");
        if (!metadata_payload.ok()) {
            return metadata_payload.status();
        }
        auto metadata_status = ValidateMetadataPayload(metadata_payload.value(), card);
        if (!metadata_status.ok()) {
            return metadata_status;
        }
        payload = metadata_payload.value();
        return Status::Ok();
    });
    if (!status.ok()) {
        return status;
    }
    return payload;
}

Result<json> FetchPredictPayloadWithRetry(const HttpClient& http_client,
                                          const fs::path& package_root,
                                          const AlgorithmCard& card,
                                          int timeout_ms,
                                          const json& request_json) {
    json payload;
    auto status = RetryUntilOk([&]() {
        auto predict_result = http_client.PostJson(
            card.machine_spec.runtime.endpoint, request_json, timeout_ms);
        if (!predict_result.ok()) {
            return predict_result.status();
        }
        auto predict_http_status = EnsureHttpSuccess(
            predict_result.value(), "POST /predict", card.machine_spec.runtime.endpoint);
        if (!predict_http_status.ok()) {
            return predict_http_status;
        }
        auto predict_payload = ParseResponseJson(predict_result.value(), "POST /predict");
        if (!predict_payload.ok()) {
            return predict_payload.status();
        }
        auto payload_status = ValidatePredictPayload(package_root, card, predict_payload.value());
        if (!payload_status.ok()) {
            return payload_status;
        }
        payload = predict_payload.value();
        return Status::Ok();
    });
    if (!status.ok()) {
        return status;
    }
    return payload;
}

}  // namespace

Status PythonServiceValidator::ValidateService(const fs::path& package_root,
                                               const AlgorithmCard& card) const {
    HttpClient http_client;
    const int timeout_ms = card.machine_spec.runtime.timeout_ms;
    std::cerr << "[TRACE] ValidateService begin: " << card.algorithm_id << '\n';

    std::cerr << "[TRACE] ValidateService call /health" << '\n';
    auto health_payload = FetchHealthPayloadWithRetry(http_client, card, timeout_ms);
    if (!health_payload.ok()) {
        return health_payload.status();
    }

    std::cerr << "[TRACE] ValidateService call /metadata" << '\n';
    auto metadata_payload = FetchMetadataPayloadWithRetry(http_client, card, timeout_ms);
    if (!metadata_payload.ok()) {
        return metadata_payload.status();
    }

    std::cerr << "[TRACE] ValidateService load golden request" << '\n';
    auto golden_request_result = LoadGoldenRequest(package_root);
    if (!golden_request_result.ok()) {
        return golden_request_result.status();
    }
    auto golden_request_status =
        ValidateGoldenRequest(package_root, card, golden_request_result.value());
    if (!golden_request_status.ok()) {
        return golden_request_status;
    }

    std::cerr << "[TRACE] ValidateService call /predict" << '\n';
    auto predict_payload = FetchPredictPayloadWithRetry(http_client,
                                                        package_root,
                                                        card,
                                                        timeout_ms,
                                                        golden_request_result.value());
    if (!predict_payload.ok()) {
        return predict_payload.status();
    }
    return Status::Ok();
}

}  // namespace algolib
