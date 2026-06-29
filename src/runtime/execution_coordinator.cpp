#include "algolib/runtime/execution_coordinator.h"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <string>
#include <utility>

namespace algolib {
namespace {

std::filesystem::path ResolveExecutionLogPath(const AlgorithmRegistry& registry,
                                              const std::filesystem::path& requested_path) {
    if (!requested_path.empty()) {
        return requested_path;
    }
    if (const char* env_value = std::getenv("ALGOLIB_EXECUTION_LOG_PATH");
        env_value != nullptr && *env_value != '\0') {
        return std::filesystem::path(env_value);
    }

    std::filesystem::path parent = registry.registry_path().parent_path();
    if (parent.empty()) {
        parent = std::filesystem::current_path();
    }
    return parent / "execution_audit.jsonl";
}

std::string GenerateId(const std::string& prefix) {
    static std::atomic<std::uint64_t> sequence{0};
    const auto now_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count();
    return prefix + "_" + std::to_string(now_ms) + "_" +
           std::to_string(sequence.fetch_add(1, std::memory_order_relaxed));
}

void EnsureRequestEnvelope(AlgorithmRequest* request) {
    if (request->request_id.empty()) {
        request->request_id = GenerateId("req");
    }
    if (request->trace_id.empty()) {
        request->trace_id = GenerateId("trace");
    }
}

AlgorithmResult BuildFailureResult(const AlgorithmRequest& request,
                                   const std::string& code,
                                   const std::string& message,
                                   nlohmann::json usage = nlohmann::json::object()) {
    AlgorithmResult result;
    result.ok = false;
    result.request_id = request.request_id;
    result.trace_id = request.trace_id;
    result.algorithm_id = request.algorithm_id;
    result.version = request.version;
    result.backend_type = request.backend_type;
    result.outputs = nlohmann::json::object();
    result.usage = usage.is_object() ? std::move(usage) : nlohmann::json::object();
    result.error = AlgorithmError{code, message};
    return result;
}

AlgorithmResult BuildFailureResult(const AlgorithmRequest& request,
                                   const Status& status,
                                   nlohmann::json usage = nlohmann::json::object()) {
    return BuildFailureResult(request, ToString(status.code()), status.message(), std::move(usage));
}

void NormalizeResultEnvelope(const AlgorithmRequest& request, AlgorithmResult* result) {
    result->request_id = result->request_id.empty() ? request.request_id : result->request_id;
    result->trace_id = result->trace_id.empty() ? request.trace_id : result->trace_id;
    result->algorithm_id =
        result->algorithm_id.empty() ? request.algorithm_id : result->algorithm_id;
    result->version = result->version.empty() ? request.version : result->version;
    result->backend_type = request.backend_type;
    if (!result->usage.is_object()) {
        result->usage = nlohmann::json::object();
    }
}

void EnsureLatency(AlgorithmResult* result, std::int64_t latency_ms) {
    if (!result->usage.is_object()) {
        result->usage = nlohmann::json::object();
    }
    if (!result->usage.contains("latency_ms")) {
        result->usage["latency_ms"] = latency_ms;
    }
}

}  // namespace

ExecutionCoordinator::ExecutionCoordinator(const AlgorithmRegistry& registry,
                                           std::filesystem::path execution_log_path)
    : registry_(registry),
      execution_logger_(ResolveExecutionLogPath(registry, execution_log_path)) {}

AlgorithmResult ExecutionCoordinator::Run(const AlgorithmRequest& request) {
    AlgorithmRequest effective_request = request;
    EnsureRequestEnvelope(&effective_request);

    const auto started_at = std::chrono::steady_clock::now();
    const auto finalize = [this, &effective_request, started_at](AlgorithmResult result) {
        NormalizeResultEnvelope(effective_request, &result);
        const auto latency_ms =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - started_at)
                .count();
        EnsureLatency(&result, latency_ms);
        const Status log_status =
            execution_logger_.Append(effective_request, result, latency_ms);
        (void)log_status;
        return result;
    };

    const AlgorithmKey key{
        effective_request.algorithm_id,
        effective_request.version,
        effective_request.backend_type,
    };

    auto entry_result = registry_.Get(key);
    if (!entry_result.ok()) {
        return finalize(BuildFailureResult(effective_request, entry_result.status()));
    }

    const AlgorithmEntry& entry = entry_result.value();
    if (entry.status != AlgorithmStatus::kActive) {
        return finalize(BuildFailureResult(
            effective_request,
            Status::Error(ErrorCode::kAlgorithmNotActive,
                          "Algorithm is not active: " + key.ToUniqueString())));
    }

    auto input_status = registry_.ValidateInputPayload(key, effective_request.inputs);
    if (!input_status.ok()) {
        return finalize(BuildFailureResult(effective_request, input_status));
    }

    auto runner = runtime_factory_.Create(effective_request.backend_type);
    if (!runner) {
        return finalize(BuildFailureResult(
            effective_request,
            Status::Error(
                ErrorCode::kUnsupportedBackendType,
                "No runtime runner is registered for backend_type=" +
                    ToString(effective_request.backend_type) + ".")));
    }

    auto load_status = runner->Load(entry);
    if (!load_status.ok()) {
        return finalize(BuildFailureResult(effective_request, load_status));
    }

    AlgorithmResult result = runner->Run(effective_request);
    NormalizeResultEnvelope(effective_request, &result);
    if (!result.ok) {
        if (!result.error.has_value()) {
            result.error = AlgorithmError{
                ToString(ErrorCode::kInvalidArgument),
                "Runner returned ok=false without an error payload.",
            };
        }
        return finalize(std::move(result));
    }

    auto output_status = registry_.ValidateOutputPayload(key, result.outputs);
    if (!output_status.ok()) {
        return finalize(BuildFailureResult(effective_request, output_status, result.usage));
    }

    return finalize(std::move(result));
}

const std::filesystem::path& ExecutionCoordinator::execution_log_path() const {
    return execution_logger_.log_path();
}

}  // namespace algolib
