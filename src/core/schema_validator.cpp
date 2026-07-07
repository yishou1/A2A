#include "algolib/core/schema_validator.h"

#include <algorithm>
#include <string>
#include <vector>

#include "algolib/io/file_utils.h"
#include "algolib/io/json_utils.h"

namespace algolib {
namespace {

using nlohmann::json;

// 中文注释：当前只支持 SPEC Phase 2 约定的基础 JSON Schema 子集。
bool IsSupportedTypeName(const std::string& type_name) {
    static const std::vector<std::string> kSupportedTypes = {
        "object",
        "array",
        "string",
        "number",
        "integer",
        "boolean",
        "null",
    };

    return std::find(kSupportedTypes.begin(), kSupportedTypes.end(), type_name) !=
           kSupportedTypes.end();
}

bool SchemaHasObjectKeywords(const json& schema_json) {
    return schema_json.contains("properties") || schema_json.contains("required") ||
           schema_json.contains("additionalProperties");
}

bool SchemaHasArrayKeywords(const json& schema_json) {
    return schema_json.contains("items") || schema_json.contains("minItems") ||
           schema_json.contains("maxItems");
}

bool SchemaHasStringKeywords(const json& schema_json) {
    return schema_json.contains("minLength") || schema_json.contains("maxLength");
}

bool SchemaHasNumericKeywords(const json& schema_json) {
    return schema_json.contains("minimum") || schema_json.contains("maximum");
}

Status MakeSchemaError(ErrorCode invalid_schema_code,
                       const std::string& location,
                       const std::string& message) {
    return Status::Error(invalid_schema_code, location + ": " + message);
}

Status ValidateNumberKeyword(const json& schema_json,
                             const char* keyword,
                             ErrorCode invalid_schema_code,
                             const std::string& schema_name) {
    if (!schema_json.contains(keyword)) {
        return Status::Ok();
    }
    if (!schema_json.at(keyword).is_number()) {
        return MakeSchemaError(invalid_schema_code, schema_name,
                               std::string(keyword) + " must be numeric.");
    }
    return Status::Ok();
}

Status ValidateNonNegativeIntegerKeyword(const json& schema_json,
                                         const char* keyword,
                                         ErrorCode invalid_schema_code,
                                         const std::string& schema_name) {
    if (!schema_json.contains(keyword)) {
        return Status::Ok();
    }
    if (!schema_json.at(keyword).is_number_integer() ||
        schema_json.at(keyword).get<int>() < 0) {
        return MakeSchemaError(invalid_schema_code, schema_name,
                               std::string(keyword) + " must be a non-negative integer.");
    }
    return Status::Ok();
}

Status ValidateSchemaNode(const json& schema_json,
                          ErrorCode invalid_schema_code,
                          const std::string& schema_name) {
    if (!schema_json.is_object()) {
        return MakeSchemaError(invalid_schema_code, schema_name,
                               "Schema node must be a JSON object.");
    }

    if (schema_json.contains("type")) {
        if (!schema_json.at("type").is_string()) {
            return MakeSchemaError(invalid_schema_code, schema_name,
                                   "type must be a string in the supported subset.");
        }
        const std::string type_name = schema_json.at("type").get<std::string>();
        if (!IsSupportedTypeName(type_name)) {
            return MakeSchemaError(invalid_schema_code, schema_name,
                                   "Unsupported type: " + type_name + ".");
        }
    }

    if (schema_json.contains("enum")) {
        if (!schema_json.at("enum").is_array() || schema_json.at("enum").empty()) {
            return MakeSchemaError(invalid_schema_code, schema_name,
                                   "enum must be a non-empty array.");
        }
    }

    auto min_length_status =
        ValidateNonNegativeIntegerKeyword(schema_json, "minLength",
                                          invalid_schema_code, schema_name);
    if (!min_length_status.ok()) {
        return min_length_status;
    }
    auto max_length_status =
        ValidateNonNegativeIntegerKeyword(schema_json, "maxLength",
                                          invalid_schema_code, schema_name);
    if (!max_length_status.ok()) {
        return max_length_status;
    }
    auto min_items_status =
        ValidateNonNegativeIntegerKeyword(schema_json, "minItems",
                                          invalid_schema_code, schema_name);
    if (!min_items_status.ok()) {
        return min_items_status;
    }
    auto max_items_status =
        ValidateNonNegativeIntegerKeyword(schema_json, "maxItems",
                                          invalid_schema_code, schema_name);
    if (!max_items_status.ok()) {
        return max_items_status;
    }
    auto minimum_status =
        ValidateNumberKeyword(schema_json, "minimum", invalid_schema_code, schema_name);
    if (!minimum_status.ok()) {
        return minimum_status;
    }
    auto maximum_status =
        ValidateNumberKeyword(schema_json, "maximum", invalid_schema_code, schema_name);
    if (!maximum_status.ok()) {
        return maximum_status;
    }

    if (schema_json.contains("properties")) {
        if (!schema_json.at("properties").is_object()) {
            return MakeSchemaError(invalid_schema_code, schema_name,
                                   "properties must be an object.");
        }
        for (auto it = schema_json.at("properties").begin();
             it != schema_json.at("properties").end(); ++it) {
            const auto child_status = ValidateSchemaNode(
                it.value(), invalid_schema_code, schema_name + ".properties." + it.key());
            if (!child_status.ok()) {
                return child_status;
            }
        }
    }

    if (schema_json.contains("required")) {
        if (!schema_json.at("required").is_array()) {
            return MakeSchemaError(invalid_schema_code, schema_name,
                                   "required must be an array.");
        }
        for (const auto& item : schema_json.at("required")) {
            if (!item.is_string()) {
                return MakeSchemaError(invalid_schema_code, schema_name,
                                       "required items must be strings.");
            }
        }
    }

    if (schema_json.contains("additionalProperties") &&
        !schema_json.at("additionalProperties").is_boolean()) {
        return MakeSchemaError(invalid_schema_code, schema_name,
                               "additionalProperties must be boolean in the supported subset.");
    }

    if (schema_json.contains("items")) {
        if (!schema_json.at("items").is_object()) {
            return MakeSchemaError(invalid_schema_code, schema_name,
                                   "items must be an object in the supported subset.");
        }
        const auto items_status = ValidateSchemaNode(schema_json.at("items"),
                                                     invalid_schema_code,
                                                     schema_name + ".items");
        if (!items_status.ok()) {
            return items_status;
        }
    }

    return Status::Ok();
}

Status ValidateTypeConstraint(const json& instance_json,
                              const std::string& type_name,
                              ErrorCode invalid_schema_code,
                              const std::string& instance_name) {
    const bool type_matches =
        (type_name == "object" && instance_json.is_object()) ||
        (type_name == "array" && instance_json.is_array()) ||
        (type_name == "string" && instance_json.is_string()) ||
        (type_name == "number" && instance_json.is_number()) ||
        (type_name == "integer" && instance_json.is_number_integer()) ||
        (type_name == "boolean" && instance_json.is_boolean()) ||
        (type_name == "null" && instance_json.is_null());
    if (!type_matches) {
        return MakeSchemaError(invalid_schema_code, instance_name,
                               "Value does not match schema type " + type_name + ".");
    }
    return Status::Ok();
}

Status ValidateInstanceNode(const json& instance_json,
                            const json& schema_json,
                            ErrorCode invalid_schema_code,
                            const std::string& instance_name) {
    const auto schema_status =
        ValidateSchemaNode(schema_json, invalid_schema_code, instance_name + "::<schema>");
    if (!schema_status.ok()) {
        return schema_status;
    }

    if (schema_json.contains("enum")) {
        bool matched = false;
        for (const auto& enum_value : schema_json.at("enum")) {
            if (enum_value == instance_json) {
                matched = true;
                break;
            }
        }
        if (!matched) {
            return MakeSchemaError(invalid_schema_code, instance_name,
                                   "Value is not one of the allowed enum values.");
        }
    }

    if (schema_json.contains("type")) {
        const auto type_status = ValidateTypeConstraint(
            instance_json, schema_json.at("type").get<std::string>(),
            invalid_schema_code, instance_name);
        if (!type_status.ok()) {
            return type_status;
        }
    }

    if ((schema_json.value("type", std::string()) == "object" || SchemaHasObjectKeywords(schema_json)) &&
        instance_json.is_object()) {
        if (schema_json.contains("required")) {
            for (const auto& required_name_json : schema_json.at("required")) {
                const std::string required_name = required_name_json.get<std::string>();
                if (!instance_json.contains(required_name)) {
                    return MakeSchemaError(invalid_schema_code, instance_name,
                                           "Missing required property: " + required_name + ".");
                }
            }
        }

        if (schema_json.contains("additionalProperties") &&
            !schema_json.at("additionalProperties").get<bool>() &&
            schema_json.contains("properties")) {
            for (auto it = instance_json.begin(); it != instance_json.end(); ++it) {
                if (!schema_json.at("properties").contains(it.key())) {
                    return MakeSchemaError(invalid_schema_code, instance_name,
                                           "Unexpected property: " + it.key() + ".");
                }
            }
        }

        if (schema_json.contains("properties")) {
            for (auto it = schema_json.at("properties").begin();
                 it != schema_json.at("properties").end(); ++it) {
                if (!instance_json.contains(it.key())) {
                    continue;
                }
                const auto child_status = ValidateInstanceNode(
                    instance_json.at(it.key()), it.value(), invalid_schema_code,
                    instance_name + "." + it.key());
                if (!child_status.ok()) {
                    return child_status;
                }
            }
        }
    }

    if ((schema_json.value("type", std::string()) == "array" || SchemaHasArrayKeywords(schema_json)) &&
        instance_json.is_array()) {
        if (schema_json.contains("minItems") &&
            instance_json.size() < schema_json.at("minItems").get<std::size_t>()) {
            return MakeSchemaError(invalid_schema_code, instance_name,
                                   "Array length is smaller than minItems.");
        }
        if (schema_json.contains("maxItems") &&
            instance_json.size() > schema_json.at("maxItems").get<std::size_t>()) {
            return MakeSchemaError(invalid_schema_code, instance_name,
                                   "Array length is greater than maxItems.");
        }
        if (schema_json.contains("items")) {
            for (std::size_t index = 0; index < instance_json.size(); ++index) {
                const auto item_status = ValidateInstanceNode(
                    instance_json.at(index), schema_json.at("items"), invalid_schema_code,
                    instance_name + "[" + std::to_string(index) + "]");
                if (!item_status.ok()) {
                    return item_status;
                }
            }
        }
    }

    if ((schema_json.value("type", std::string()) == "string" || SchemaHasStringKeywords(schema_json)) &&
        instance_json.is_string()) {
        const auto& string_value = instance_json.get_ref<const std::string&>();
        if (schema_json.contains("minLength") &&
            string_value.size() < schema_json.at("minLength").get<std::size_t>()) {
            return MakeSchemaError(invalid_schema_code, instance_name,
                                   "String length is smaller than minLength.");
        }
        if (schema_json.contains("maxLength") &&
            string_value.size() > schema_json.at("maxLength").get<std::size_t>()) {
            return MakeSchemaError(invalid_schema_code, instance_name,
                                   "String length is greater than maxLength.");
        }
    }

    if ((schema_json.value("type", std::string()) == "number" ||
         schema_json.value("type", std::string()) == "integer" ||
         SchemaHasNumericKeywords(schema_json)) &&
        instance_json.is_number()) {
        const double numeric_value = instance_json.get<double>();
        if (schema_json.contains("minimum") &&
            numeric_value < schema_json.at("minimum").get<double>()) {
            return MakeSchemaError(invalid_schema_code, instance_name,
                                   "Numeric value is smaller than minimum.");
        }
        if (schema_json.contains("maximum") &&
            numeric_value > schema_json.at("maximum").get<double>()) {
            return MakeSchemaError(invalid_schema_code, instance_name,
                                   "Numeric value is greater than maximum.");
        }
    }

    return Status::Ok();
}

Result<json> LoadAndValidateSchema(const std::filesystem::path& schema_path,
                                   ErrorCode invalid_schema_code) {
    auto schema_result = JsonUtils::ReadJsonFile(schema_path);
    if (!schema_result.ok()) {
        return Status::Error(
            invalid_schema_code,
            "Failed to load schema " + schema_path.generic_string() + ": " +
                schema_result.status().ToString());
    }

    const auto schema_status =
        ValidateSchemaNode(schema_result.value(), invalid_schema_code, schema_path.generic_string());
    if (!schema_status.ok()) {
        return schema_status;
    }

    return schema_result.value();
}

}  // namespace

Result<json> SchemaValidator::LoadSchema(const std::filesystem::path& schema_path,
                                         ErrorCode invalid_schema_code) const {
    return LoadAndValidateSchema(schema_path, invalid_schema_code);
}

Status SchemaValidator::ValidateSchemaDocument(const json& schema_json,
                                               ErrorCode invalid_schema_code,
                                               const std::string& schema_name) const {
    return ValidateSchemaNode(schema_json, invalid_schema_code, schema_name);
}

Status SchemaValidator::ValidateInstance(const json& instance_json,
                                         const json& schema_json,
                                         ErrorCode invalid_schema_code,
                                         const std::string& instance_name) const {
    return ValidateInstanceNode(instance_json, schema_json, invalid_schema_code, instance_name);
}

Status SchemaValidator::ValidateInputForEntry(const AlgorithmEntry& entry,
                                              const json& input_json) const {
    const auto schema_path = FileUtils::ResolveReference(
        entry.package_root, entry.card.machine_spec.input_schema_ref);
    auto schema_result = LoadSchema(schema_path, ErrorCode::kInputSchemaInvalid);
    if (!schema_result.ok()) {
        return schema_result.status();
    }

    return ValidateInstance(input_json, schema_result.value(),
                            ErrorCode::kInputSchemaInvalid, "$.inputs");
}

Status SchemaValidator::ValidateOutputForEntry(const AlgorithmEntry& entry,
                                               const json& output_json) const {
    const auto schema_path = FileUtils::ResolveReference(
        entry.package_root, entry.card.machine_spec.output_schema_ref);
    auto schema_result = LoadSchema(schema_path, ErrorCode::kOutputSchemaInvalid);
    if (!schema_result.ok()) {
        return schema_result.status();
    }

    return ValidateInstance(output_json, schema_result.value(),
                            ErrorCode::kOutputSchemaInvalid, "$.outputs");
}

}  // namespace algolib
