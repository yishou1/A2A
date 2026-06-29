#include "algolib/io/json_utils.h"

#include "algolib/io/file_utils.h"

namespace algolib {

Result<nlohmann::json> JsonUtils::ReadJsonFile(const std::filesystem::path& file_path) {
    auto content_result = FileUtils::ReadTextFile(file_path);
    if (!content_result.ok()) {
        return content_result.status();
    }

    try {
        return nlohmann::json::parse(content_result.value());
    } catch (const std::exception& ex) {
        return Status::Error(
            ErrorCode::kJsonParseError,
            "Failed to parse JSON file " + file_path.generic_string() + ": " + ex.what());
    }
}

Status JsonUtils::WriteJsonFile(const std::filesystem::path& file_path,
                                const nlohmann::json& json_value) {
    return FileUtils::WriteTextFile(file_path, Pretty(json_value));
}

nlohmann::json JsonUtils::SummarizeSchema(const nlohmann::json& schema_json) {
    nlohmann::json summary;
    summary["title"] = schema_json.value("title", "");
    summary["type"] = schema_json.value("type", "");
    summary["required"] = schema_json.value("required", nlohmann::json::array());
    summary["properties"] = nlohmann::json::array();

    if (schema_json.contains("properties") && schema_json.at("properties").is_object()) {
        for (auto it = schema_json.at("properties").begin();
             it != schema_json.at("properties").end(); ++it) {
            const auto& property_schema = it.value();
            summary["properties"].push_back({
                {"name", it.key()},
                {"type", property_schema.value("type", "")},
                {"description", property_schema.value("description", "")},
            });
        }
    }

    return summary;
}

std::string JsonUtils::Dump(const nlohmann::json& json_value, int indent) {
    // 中文注释：对外部输入或 Windows 本地字符串做容错序列化，避免 debug 构建因非法 UTF-8 直接断言退出。
    return json_value.dump(indent, ' ', false, nlohmann::json::error_handler_t::replace);
}

std::string JsonUtils::Pretty(const nlohmann::json& json_value) {
    return Dump(json_value, 2);
}

}  // namespace algolib
