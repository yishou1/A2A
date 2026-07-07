#include "algolib/validation/golden_case_runner.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <string>
#include <vector>

#include "algolib/core/schema_validator.h"
#include "algolib/io/json_utils.h"
#include "algolib/runtime/algorithm_request.h"

namespace algolib {
namespace {

namespace fs = std::filesystem;
using nlohmann::json;

bool EndsWith(const std::string& value, const std::string& suffix) {
    return value.size() >= suffix.size() &&
           value.compare(value.size() - suffix.size(), suffix.size(), suffix) == 0;
}

constexpr const char* kGoldenInputSuffix = "_input.json";
constexpr const char* kGoldenExpectedSuffix = "_expected.json";

Status CompareJson(const json& expected,
                   const json& actual,
                   const std::string& path) {
    if (expected.type() != actual.type()) {
        if (expected.is_number() && actual.is_number()) {
            const double diff = std::fabs(expected.get<double>() - actual.get<double>());
            if (diff <= 1e-6) {
                return Status::Ok();
            }
        }
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "Golden case mismatch at " + path + ": JSON types differ.");
    }

    if (expected.is_object()) {
        for (auto it = expected.begin(); it != expected.end(); ++it) {
            if (!actual.contains(it.key())) {
                return Status::Error(
                    ErrorCode::kGoldenCaseFailed,
                    "Golden case mismatch at " + path + ": missing key `" + it.key() + "`.");
            }
            auto child_status =
                CompareJson(it.value(), actual.at(it.key()), path + "." + it.key());
            if (!child_status.ok()) {
                return child_status;
            }
        }
        for (auto it = actual.begin(); it != actual.end(); ++it) {
            if (!expected.contains(it.key())) {
                return Status::Error(
                    ErrorCode::kGoldenCaseFailed,
                    "Golden case mismatch at " + path + ": unexpected key `" + it.key() + "`.");
            }
        }
        return Status::Ok();
    }

    if (expected.is_array()) {
        if (expected.size() != actual.size()) {
            return Status::Error(
                ErrorCode::kGoldenCaseFailed,
                "Golden case mismatch at " + path + ": array sizes differ.");
        }
        for (std::size_t index = 0; index < expected.size(); ++index) {
            auto child_status = CompareJson(expected.at(index), actual.at(index),
                                            path + "[" + std::to_string(index) + "]");
            if (!child_status.ok()) {
                return child_status;
            }
        }
        return Status::Ok();
    }

    if (expected.is_number()) {
        const double diff = std::fabs(expected.get<double>() - actual.get<double>());
        if (diff <= 1e-6) {
            return Status::Ok();
        }
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "Golden case mismatch at " + path + ": numeric values differ.");
    }

    if (expected != actual) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "Golden case mismatch at " + path + ": values differ.");
    }
    return Status::Ok();
}

Status ValidateGoldenInputAgainstSchema(const AlgorithmEntry& entry, const json& input_json) {
    SchemaValidator schema_validator;
    return schema_validator.ValidateInputForEntry(entry, input_json);
}

Status ValidateGoldenOutputAgainstSchema(const AlgorithmEntry& entry, const json& output_json) {
    SchemaValidator schema_validator;
    return schema_validator.ValidateOutputForEntry(entry, output_json);
}

}  // namespace

Status GoldenCaseRunner::Run(const AlgorithmEntry& entry, IAlgorithmRunner* runner) const {
    if (runner == nullptr) {
        return Status::Error(ErrorCode::kGoldenCaseFailed,
                             "GoldenCaseRunner requires a non-null runner.");
    }

    const fs::path golden_dir = entry.package_root / "golden_cases";
    if (!fs::exists(golden_dir) || !fs::is_directory(golden_dir)) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "golden_cases directory was not found: " + golden_dir.generic_string());
    }

    std::vector<fs::path> input_case_paths;
    for (const auto& item : fs::directory_iterator(golden_dir)) {
        if (!item.is_regular_file()) {
            continue;
        }
        const std::string filename = item.path().filename().generic_string();
        if (EndsWith(filename, kGoldenInputSuffix)) {
            input_case_paths.push_back(item.path());
        }
    }

    std::sort(input_case_paths.begin(), input_case_paths.end());
    if (input_case_paths.empty()) {
        return Status::Error(
            ErrorCode::kGoldenCaseFailed,
            "At least one *_input.json golden case is required.");
    }

    for (const auto& input_case_path : input_case_paths) {
        const std::string expected_filename =
            input_case_path.filename().generic_string().substr(
                0, input_case_path.filename().generic_string().size() -
                       std::char_traits<char>::length(kGoldenInputSuffix)) +
            kGoldenExpectedSuffix;
        const fs::path expected_case_path = input_case_path.parent_path() / expected_filename;
        if (!fs::exists(expected_case_path)) {
            return Status::Error(
                ErrorCode::kGoldenCaseFailed,
                "Golden expected file was not found: " + expected_case_path.generic_string());
        }

        auto input_result = JsonUtils::ReadJsonFile(input_case_path);
        if (!input_result.ok()) {
            return Status::Error(
                ErrorCode::kGoldenCaseFailed,
                "Failed to read golden input " + input_case_path.generic_string() + ": " +
                    input_result.status().ToString());
        }
        auto expected_result = JsonUtils::ReadJsonFile(expected_case_path);
        if (!expected_result.ok()) {
            return Status::Error(
                ErrorCode::kGoldenCaseFailed,
                "Failed to read golden expected output " +
                    expected_case_path.generic_string() + ": " +
                    expected_result.status().ToString());
        }

        auto input_schema_status =
            ValidateGoldenInputAgainstSchema(entry, input_result.value());
        if (!input_schema_status.ok()) {
            return input_schema_status;
        }

        AlgorithmRequest request;
        request.request_id = "golden_case";
        request.trace_id = input_case_path.stem().generic_string();
        request.algorithm_id = entry.key.algorithm_id;
        request.version = entry.key.version;
        request.backend_type = entry.key.backend_type;
        request.inputs = input_result.value();

        const AlgorithmResult result = runner->Run(request);
        if (!result.ok) {
            std::string message = "Golden case execution failed.";
            if (result.error.has_value()) {
                message += " " + result.error->code + ": " + result.error->message;
            }
            return Status::Error(ErrorCode::kGoldenCaseFailed, message);
        }

        auto output_schema_status =
            ValidateGoldenOutputAgainstSchema(entry, result.outputs);
        if (!output_schema_status.ok()) {
            return output_schema_status;
        }

        auto compare_status =
            CompareJson(expected_result.value(), result.outputs, "$");
        if (!compare_status.ok()) {
            return compare_status;
        }
    }

    return Status::Ok();
}

}  // namespace algolib
