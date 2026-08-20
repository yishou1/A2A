#include "algolib/runtime/execution_logger.h"

#include <fstream>
#include <utility>

#include "algolib/io/file_utils.h"
#include "algolib/io/json_utils.h"
#include "algolib/io/sha256.h"

namespace algolib {

ExecutionLogger::ExecutionLogger(std::filesystem::path log_path)
    : log_path_(std::move(log_path)) {}

Status ExecutionLogger::Append(const AlgorithmRequest& request,
                               const AlgorithmResult& result,
                               std::int64_t latency_ms) const {
    auto ensure_status = FileUtils::EnsureParentDirectory(log_path_);
    if (!ensure_status.ok()) {
        return ensure_status;
    }

    std::ofstream output_stream(log_path_,
                                std::ios::out | std::ios::binary | std::ios::app);
    if (!output_stream.is_open()) {
        return Status::Error(
            ErrorCode::kIoError,
            "Unable to append execution log: " + log_path_.generic_string());
    }

    nlohmann::json log_record{
        {"request_id", result.request_id},
        {"trace_id", result.trace_id},
        {"algorithm_id", result.algorithm_id},
        {"version", result.version},
        {"backend_type", ToString(result.backend_type)},
        {"status", result.ok ? "success" : "failure"},
        {"latency_ms", latency_ms},
        {"error_code", result.error.has_value() ? nlohmann::json(result.error->code)
                                                : nlohmann::json(nullptr)},
        {"input_hash", "sha256:" + ComputeSha256Hex(JsonUtils::Dump(request.inputs))},
        {"output_hash", "sha256:" + ComputeSha256Hex(JsonUtils::Dump(result.outputs))},
    };

    output_stream << JsonUtils::Dump(log_record) << '\n';
    return Status::Ok();
}

const std::filesystem::path& ExecutionLogger::log_path() const {
    return log_path_;
}

}  // namespace algolib
