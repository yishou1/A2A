#pragma once

#include <filesystem>
#include <string>

#include "algolib/core/status.h"

namespace algolib {

// 中文注释: 统一处理本地路径 file:// URI 和基础文件读写
class FileUtils {
public:
    static Result<std::filesystem::path> NormalizeInputPath(
        const std::filesystem::path& raw_path);

    static Result<std::filesystem::path> ResolveCardPath(
        const std::filesystem::path& package_or_card_path);

    static std::filesystem::path ResolveReference(
        const std::filesystem::path& base_directory,
        const std::string& relative_or_absolute_path);

    static Status EnsureParentDirectory(const std::filesystem::path& file_path);
    static Result<std::string> ReadTextFile(const std::filesystem::path& file_path);
    static Status WriteTextFile(const std::filesystem::path& file_path,
                                const std::string& content);
};

}  // namespace algolib
