#include "algolib/io/file_utils.h"

#include <cctype>
#include <fstream>
#include <sstream>

namespace algolib {
namespace {

std::string StripFileScheme(const std::string& raw_value) {
    constexpr const char* kFileSchemeLong = "file://";
    constexpr const char* kFileSchemeShort = "file:/";

    std::string stripped = raw_value;
    if (raw_value.rfind(kFileSchemeLong, 0) == 0) {
        stripped = raw_value.substr(std::char_traits<char>::length(kFileSchemeLong));
    } else if (raw_value.rfind(kFileSchemeShort, 0) == 0) {
        stripped = raw_value.substr(std::char_traits<char>::length(kFileSchemeShort));
    } else {
        return raw_value;
    }
    if (stripped.size() >= 3 && stripped[0] == '/' &&
        std::isalpha(static_cast<unsigned char>(stripped[1])) && stripped[2] == ':') {
        stripped.erase(stripped.begin());
    }
    return stripped;
}

}  // namespace

Result<std::filesystem::path> FileUtils::NormalizeInputPath(
    const std::filesystem::path& raw_path) {
    const std::string raw_text = raw_path.generic_string();
    const std::filesystem::path normalized = StripFileScheme(raw_text);
    const std::filesystem::path absolute_path = std::filesystem::absolute(normalized);
    if (!std::filesystem::exists(absolute_path)) {
        return Status::Error(ErrorCode::kIoError,
                             "Path does not exist: " + absolute_path.generic_string());
    }
    return std::filesystem::weakly_canonical(absolute_path);
}

Result<std::filesystem::path> FileUtils::ResolveCardPath(
    const std::filesystem::path& package_or_card_path) {
    auto normalized_result = NormalizeInputPath(package_or_card_path);
    if (!normalized_result.ok()) {
        return normalized_result.status();
    }

    std::filesystem::path resolved_path = normalized_result.value();
    if (std::filesystem::is_directory(resolved_path)) {
        resolved_path /= "algorithm_card.yaml";
    }

    if (!std::filesystem::exists(resolved_path)) {
        return Status::Error(
            ErrorCode::kMissingRequiredFile,
            "algorithm_card.yaml was not found at: " + resolved_path.generic_string());
    }

    return std::filesystem::weakly_canonical(resolved_path);
}

std::filesystem::path FileUtils::ResolveReference(
    const std::filesystem::path& base_directory,
    const std::string& relative_or_absolute_path) {
    std::filesystem::path candidate(relative_or_absolute_path);
    if (candidate.is_absolute()) {
        return candidate;
    }
    return std::filesystem::weakly_canonical(base_directory / candidate);
}

Status FileUtils::EnsureParentDirectory(const std::filesystem::path& file_path) {
    try {
        const auto parent_path = file_path.parent_path();
        if (!parent_path.empty()) {
            std::filesystem::create_directories(parent_path);
        }
        return Status::Ok();
    } catch (const std::exception& ex) {
        return Status::Error(ErrorCode::kIoError, ex.what());
    }
}

Result<std::string> FileUtils::ReadTextFile(const std::filesystem::path& file_path) {
    std::ifstream input_stream(file_path, std::ios::in | std::ios::binary);
    if (!input_stream.is_open()) {
        return Status::Error(ErrorCode::kIoError,
                             "Unable to open file: " + file_path.generic_string());
    }

    std::ostringstream buffer;
    buffer << input_stream.rdbuf();
    return buffer.str();
}

Status FileUtils::WriteTextFile(const std::filesystem::path& file_path,
                                const std::string& content) {
    auto ensure_status = EnsureParentDirectory(file_path);
    if (!ensure_status.ok()) {
        return ensure_status;
    }

    std::ofstream output_stream(file_path, std::ios::out | std::ios::binary | std::ios::trunc);
    if (!output_stream.is_open()) {
        return Status::Error(ErrorCode::kIoError,
                             "Unable to write file: " + file_path.generic_string());
    }

    output_stream << content;
    return Status::Ok();
}

}  // namespace algolib
