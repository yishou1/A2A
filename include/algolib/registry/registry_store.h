#pragma once

#include <filesystem>
#include <map>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"

namespace algolib {

// 中文注释: RegistryStore 负责把注册表条目落盘到单个 JSON 文件
class RegistryStore {
public:
    explicit RegistryStore(std::filesystem::path registry_path);

    Status Load(std::map<AlgorithmKey, AlgorithmEntry>* entries) const;
    Status Save(const std::map<AlgorithmKey, AlgorithmEntry>& entries) const;

    const std::filesystem::path& registry_path() const;

private:
    std::filesystem::path registry_path_;
};

}  // namespace algolib
