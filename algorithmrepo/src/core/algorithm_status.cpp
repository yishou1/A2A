#include "algolib/core/algorithm_status.h"

#include <algorithm>
#include <cctype>
#include <string>

namespace algolib {
namespace {

std::string ToLower(std::string_view raw_value) {
    std::string normalized(raw_value);
    std::transform(normalized.begin(), normalized.end(), normalized.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    return normalized;
}

}  // namespace

std::string ToString(AlgorithmStatus status) {
    switch (status) {
        case AlgorithmStatus::kDraft:
            return "draft";
        case AlgorithmStatus::kValidated:
            return "validated";
        case AlgorithmStatus::kActive:
            return "active";
        case AlgorithmStatus::kDisabled:
            return "disabled";
        case AlgorithmStatus::kDeleted:
            return "deleted";
    }

    return "unknown";
}

Result<AlgorithmStatus> ParseAlgorithmStatus(std::string_view raw_value) {
    const std::string normalized = ToLower(raw_value);
    if (normalized == "draft") {
        return AlgorithmStatus::kDraft;
    }
    if (normalized == "validated") {
        return AlgorithmStatus::kValidated;
    }
    if (normalized == "active") {
        return AlgorithmStatus::kActive;
    }
    if (normalized == "disabled") {
        return AlgorithmStatus::kDisabled;
    }
    if (normalized == "deleted") {
        return AlgorithmStatus::kDeleted;
    }

    return Status::Error(
        ErrorCode::kInvalidAlgorithmCard,
        "Unsupported algorithm status: " + std::string(raw_value) + ".");
}

}  // namespace algolib
