#include "algolib/io/yaml_utils.h"

#include <cstdint>

namespace algolib {
namespace {

bool TryConvertToBool(const YAML::Node& node, bool* value) {
    try {
        *value = node.as<bool>();
        return true;
    } catch (...) {
        return false;
    }
}

bool TryConvertToInt64(const YAML::Node& node, std::int64_t* value) {
    try {
        *value = node.as<std::int64_t>();
        return true;
    } catch (...) {
        return false;
    }
}

bool TryConvertToDouble(const YAML::Node& node, double* value) {
    try {
        *value = node.as<double>();
        return true;
    } catch (...) {
        return false;
    }
}

}  // namespace

Result<YAML::Node> YamlUtils::LoadYamlFile(const std::filesystem::path& file_path) {
    try {
        return YAML::LoadFile(file_path.string());
    } catch (const std::exception& ex) {
        return Status::Error(
            ErrorCode::kYamlParseError,
            "Failed to parse YAML file " + file_path.generic_string() + ": " + ex.what());
    }
}

nlohmann::json YamlUtils::YamlNodeToJson(const YAML::Node& node) {
    if (!node || node.IsNull()) {
        return nullptr;
    }
    if (node.IsSequence()) {
        nlohmann::json array_value = nlohmann::json::array();
        for (const auto& item : node) {
            array_value.push_back(YamlUtils::YamlNodeToJson(item));
        }
        return array_value;
    }
    if (node.IsMap()) {
        nlohmann::json object_value = nlohmann::json::object();
        for (const auto& item : node) {
            object_value[item.first.as<std::string>()] = YamlUtils::YamlNodeToJson(item.second);
        }
        return object_value;
    }

    bool bool_value = false;
    if (TryConvertToBool(node, &bool_value)) {
        return bool_value;
    }

    std::int64_t int_value = 0;
    if (TryConvertToInt64(node, &int_value)) {
        return int_value;
    }

    double double_value = 0.0;
    if (TryConvertToDouble(node, &double_value)) {
        return double_value;
    }

    return node.as<std::string>();
}

}  // namespace algolib
