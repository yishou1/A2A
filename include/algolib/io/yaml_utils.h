#pragma once

#include <filesystem>

#include <nlohmann/json.hpp>
#include <yaml-cpp/yaml.h>

#include "algolib/core/status.h"

namespace algolib {

class YamlUtils {  // 中文注释: YAML 工具只承担 algorithm_card.yaml 的加载和节点到 JSON 的桥接
public:
    static Result<YAML::Node> LoadYamlFile(const std::filesystem::path& file_path);
    static nlohmann::json YamlNodeToJson(const YAML::Node& node);
};

}  // namespace algolib
