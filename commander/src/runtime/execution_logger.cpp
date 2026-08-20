#include "algolib/runtime/execution_logger.h"

#include <fstream>
#include <chrono>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <utility>

#include "algolib/io/file_utils.h"
#include "algolib/io/json_utils.h"
#include "algolib/io/sha256.h"

namespace algolib {
namespace {

std::string UtcTimestampNow() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t now_time = std::chrono::system_clock::to_time_t(now);
    std::tm utc{};
#ifdef _WIN32
    gmtime_s(&utc, &now_time);
#else
    gmtime_r(&now_time, &utc);
#endif
    std::ostringstream output;
    output << std::put_time(&utc, "%Y-%m-%dT%H:%M:%SZ");
    return output.str();
}

}  // namespace

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
        {"recorded_at", UtcTimestampNow()},
    };
    if (result.function_execution.has_value()) {
        log_record["function_execution"] =
            ToJson(result).at("function_execution");
    }

    output_stream << JsonUtils::Dump(log_record) << '\n';
    return Status::Ok();
}

Result<std::vector<nlohmann::json>> ExecutionLogger::ReadFunctionExecutions(
    const std::string& trace_id) const {
    if (trace_id.empty()) {
        return Status::Error(ErrorCode::kInvalidArgument,
                             "trace_id must not be empty.");
    }

    std::vector<nlohmann::json> events;
    if (!std::filesystem::exists(log_path_)) {
        return events;
    }

    std::ifstream input_stream(log_path_, std::ios::in | std::ios::binary);
    if (!input_stream.is_open()) {
        return Status::Error(ErrorCode::kIoError,
                             "Unable to read execution log: " +
                                 log_path_.generic_string());
    }

    std::string line;
    std::size_t sequence = 0;
    while (std::getline(input_stream, line)) {
        if (line.empty()) {
            continue;
        }
        try {
            const nlohmann::json record = nlohmann::json::parse(line);
            if (record.value("trace_id", std::string()) != trace_id ||
                !record.contains("function_execution") ||
                !record.at("function_execution").is_object()) {
                continue;
            }
            nlohmann::json event{
                {"sequence", ++sequence},
                {"request_id", record.value("request_id", std::string())},
                {"trace_id", trace_id},
                {"algorithm_id", record.value("algorithm_id", std::string())},
                {"version", record.value("version", std::string())},
                {"backend_type", record.value("backend_type", std::string())},
                {"status", record.value("status", std::string())},
                {"latency_ms", record.value("latency_ms", 0)},
                {"error_code", record.value("error_code", nlohmann::json(nullptr))},
                {"recorded_at", record.value("recorded_at", std::string())},
                {"function_execution", record.at("function_execution")},
            };
            events.push_back(std::move(event));
        } catch (const std::exception&) {
            // Audit reading remains available even if an unrelated legacy line is malformed.
        }
    }
    return events;
}

const std::filesystem::path& ExecutionLogger::log_path() const {
    return log_path_;
}

}  // namespace algolib
