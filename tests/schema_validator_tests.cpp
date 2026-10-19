#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/core/schema_validator.h"
#include "algolib/io/json_utils.h"
#include "algolib/registry/algorithm_registry.h"

namespace {

namespace fs = std::filesystem;
using algolib::AlgorithmKey;
using algolib::AlgorithmRegistry;
using algolib::BackendType;
using algolib::ErrorCode;
using algolib::JsonUtils;
using algolib::SchemaValidator;

// 中文注释：测试保持轻量自校验风格，便于和现有自定义测试入口保持一致。
void Expect(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

fs::path SourceRoot() {
    return fs::path(ALGOLIB_SOURCE_DIR);
}

fs::path MakeTempDir(const std::string& name) {
    fs::path temp_dir = fs::temp_directory_path() / ("algolib_schema_" + name);
    std::error_code ec;
    fs::remove_all(temp_dir, ec);
    fs::create_directories(temp_dir);
    return temp_dir;
}

AlgorithmKey OnnxKey() {
    return AlgorithmKey{"onnx_text_classifier", "1.0.0", BackendType::kOnnx};
}

void TestSchemaValidatorSupportsNestedObjectsAndArrays() {
    SchemaValidator validator;
    const nlohmann::json schema_json = {
        {"type", "object"},
        {"required", {"items", "mode"}},
        {"additionalProperties", false},
        {"properties",
         {
             {"mode", {{"type", "string"}, {"enum", {"strict", "relaxed"}}}},
             {"items",
              {{"type", "array"},
               {"minItems", 1},
               {"items",
                {{"type", "object"},
                 {"required", {"name", "score"}},
                 {"properties",
                  {
                      {"name", {{"type", "string"}, {"minLength", 1}}},
                      {"score", {{"type", "number"}, {"minimum", 0}, {"maximum", 1}}},
                  }}}}}},
         }},
    };

    auto schema_status =
        validator.ValidateSchemaDocument(schema_json, ErrorCode::kInputSchemaInvalid, "$");
    Expect(schema_status.ok(), "Nested schema should be accepted.");

    const nlohmann::json valid_payload = {
        {"mode", "strict"},
        {"items", {{{"name", "alpha"}, {"score", 0.8}}}},
    };
    auto valid_status =
        validator.ValidateInstance(valid_payload, schema_json,
                                   ErrorCode::kInputSchemaInvalid, "$.inputs");
    Expect(valid_status.ok(), "Nested payload should pass validation.");

    const nlohmann::json invalid_payload = {
        {"mode", "strict"},
        {"items", {{{"name", ""}, {"score", 2.0}}}},
    };
    auto invalid_status =
        validator.ValidateInstance(invalid_payload, schema_json,
                                   ErrorCode::kInputSchemaInvalid, "$.inputs");
    Expect(!invalid_status.ok(), "Invalid nested payload should fail validation.");
    Expect(invalid_status.code() == ErrorCode::kInputSchemaInvalid,
           "Invalid nested payload should map to INPUT_SCHEMA_INVALID.");
}

void TestSchemaValidatorSupportsNullableUnionTypes() {
    SchemaValidator validator;
    const nlohmann::json schema_json = {
        {"type", "object"},
        {"required", {"target_id"}},
        {"properties",
         {
             {"target_id", {{"type", {"string", "null"}}}},
         }},
    };

    auto schema_status =
        validator.ValidateSchemaDocument(schema_json, ErrorCode::kOutputSchemaInvalid, "$");
    Expect(schema_status.ok(), "Nullable union schema should be accepted.");

    auto string_status = validator.ValidateInstance(
        nlohmann::json{{"target_id", "T-001"}}, schema_json,
        ErrorCode::kOutputSchemaInvalid, "$.outputs");
    Expect(string_status.ok(), "String value should match nullable union type.");

    auto null_status = validator.ValidateInstance(
        nlohmann::json{{"target_id", nullptr}}, schema_json,
        ErrorCode::kOutputSchemaInvalid, "$.outputs");
    Expect(null_status.ok(), "Null value should match nullable union type.");

    auto number_status = validator.ValidateInstance(
        nlohmann::json{{"target_id", 42}}, schema_json,
        ErrorCode::kOutputSchemaInvalid, "$.outputs");
    Expect(!number_status.ok(), "Number value should not match nullable union type.");
}

void TestSchemaValidatorRejectsMalformedUnionTypes() {
    SchemaValidator validator;
    const nlohmann::json duplicate_schema = {{"type", {"string", "string"}}};
    auto duplicate_status = validator.ValidateSchemaDocument(
        duplicate_schema, ErrorCode::kInputSchemaInvalid, "$");
    Expect(!duplicate_status.ok(), "Duplicate union types should be rejected.");

    const nlohmann::json non_string_schema = {{"type", {"string", 1}}};
    auto non_string_status = validator.ValidateSchemaDocument(
        non_string_schema, ErrorCode::kInputSchemaInvalid, "$");
    Expect(!non_string_status.ok(), "Non-string union type entries should be rejected.");
}

void TestSchemaSummaryPreservesUnionTypes() {
    const nlohmann::json schema_json = {
        {"type", "object"},
        {"properties",
         {
             {"target_id", {{"type", {"string", "null"}}}},
         }},
    };
    const auto summary = JsonUtils::SummarizeSchema(schema_json);
    Expect(summary.at("properties").at(0).at("type") ==
               nlohmann::json({"string", "null"}),
           "Schema summary should preserve an array-valued union type.");
}

void TestXbdOutputSchemaAcceptsGoldenOutput() {
    SchemaValidator validator;
    const fs::path package_root =
        SourceRoot() / "examples" / "xbd_damage_assessor" / "1.0.0";
    auto schema_result = validator.LoadSchema(
        package_root / "output.schema.json", ErrorCode::kOutputSchemaInvalid);
    Expect(schema_result.ok(), "xBD output schema should load.");
    auto response_result = JsonUtils::ReadJsonFile(
        package_root / "golden_cases" / "case_001_response.json");
    Expect(response_result.ok(), "xBD golden response should load.");
    auto output_status = validator.ValidateInstance(
        response_result.value().at("outputs"), schema_result.value(),
        ErrorCode::kOutputSchemaInvalid, "$.outputs");
    Expect(output_status.ok(), "xBD golden output should satisfy nullable output schema.");
}

void TestRegistryInputAndOutputValidation() {
    fs::path temp_dir = MakeTempDir("registry_input_output");
    fs::path registry_path = temp_dir / "registry.json";

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for schema validation test.");

    auto register_result =
        registry.Register(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0");
    Expect(register_result.ok(), "Fixture should register successfully.");

    auto valid_input_status = registry.ValidateInputPayload(
        OnnxKey(), nlohmann::json{{"text", "hello world"}});
    Expect(valid_input_status.ok(), "Valid input payload should pass.");

    auto invalid_input_status = registry.ValidateInputPayload(
        OnnxKey(), nlohmann::json{{"text", 123}});
    Expect(!invalid_input_status.ok(), "Wrong input type should fail.");
    Expect(invalid_input_status.code() == ErrorCode::kInputSchemaInvalid,
           "Wrong input type should map to INPUT_SCHEMA_INVALID.");

    auto valid_output_status = registry.ValidateOutputPayload(
        OnnxKey(), nlohmann::json{{"label", "task"}, {"confidence", 0.95}});
    Expect(valid_output_status.ok(), "Valid output payload should pass.");

    auto invalid_output_status = registry.ValidateOutputPayload(
        OnnxKey(), nlohmann::json{{"label", "task"}});
    Expect(!invalid_output_status.ok(), "Missing output field should fail.");
    Expect(invalid_output_status.code() == ErrorCode::kOutputSchemaInvalid,
           "Missing output field should map to OUTPUT_SCHEMA_INVALID.");
}

void TestRegisterFailsWhenSchemaDocumentUsesUnsupportedSubset() {
    fs::path temp_dir = MakeTempDir("invalid_schema_document");
    fs::path registry_path = temp_dir / "registry.json";
    fs::path source_dir =
        SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0";
    fs::path fixture_dir = temp_dir / "fixture";
    fs::copy(source_dir, fixture_dir,
             fs::copy_options::recursive | fs::copy_options::overwrite_existing);

    const nlohmann::json invalid_schema = {
        {"type", "object"},
        {"required", {"text"}},
        {"properties",
         {
             {"text", {{"type", "string"}}},
        }},
        {"items", nlohmann::json::array()},
    };

    std::ofstream output_stream(fixture_dir / "input.schema.json",
                                std::ios::out | std::ios::binary | std::ios::trunc);
    Expect(output_stream.is_open(), "Fixture schema file should be writable.");
    output_stream << invalid_schema.dump(2);
    output_stream.close();

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for invalid schema document test.");

    auto register_result = registry.Register(fixture_dir);
    Expect(!register_result.ok(), "Register should fail for unsupported schema subset.");
    Expect(register_result.status().code() == ErrorCode::kInputSchemaInvalid,
           "Unsupported schema subset should map to INPUT_SCHEMA_INVALID.");
}

void TestRegisterFailsWhenSchemaJsonIsMalformed() {
    fs::path temp_dir = MakeTempDir("malformed_schema_document");
    fs::path registry_path = temp_dir / "registry.json";
    fs::path source_dir =
        SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0";
    fs::path fixture_dir = temp_dir / "fixture";
    fs::copy(source_dir, fixture_dir,
             fs::copy_options::recursive | fs::copy_options::overwrite_existing);

    std::ofstream output_stream(fixture_dir / "input.schema.json",
                                std::ios::out | std::ios::binary | std::ios::trunc);
    Expect(output_stream.is_open(), "Fixture schema file should be writable.");
    output_stream << "{\n  \"type\": \"object\",\n  \"properties\": {\n    \"text\": ";
    output_stream.close();

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for malformed schema test.");

    auto register_result = registry.Register(fixture_dir);
    Expect(!register_result.ok(), "Register should fail for malformed schema JSON.");
    Expect(register_result.status().code() == ErrorCode::kInputSchemaInvalid,
           "Malformed schema JSON should map to INPUT_SCHEMA_INVALID.");
}

}  // namespace

int RunSchemaValidatorTests() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"TestSchemaValidatorSupportsNestedObjectsAndArrays",
         TestSchemaValidatorSupportsNestedObjectsAndArrays},
        {"TestSchemaValidatorSupportsNullableUnionTypes",
         TestSchemaValidatorSupportsNullableUnionTypes},
        {"TestSchemaValidatorRejectsMalformedUnionTypes",
         TestSchemaValidatorRejectsMalformedUnionTypes},
        {"TestSchemaSummaryPreservesUnionTypes",
         TestSchemaSummaryPreservesUnionTypes},
        {"TestXbdOutputSchemaAcceptsGoldenOutput",
         TestXbdOutputSchemaAcceptsGoldenOutput},
        {"TestRegistryInputAndOutputValidation",
         TestRegistryInputAndOutputValidation},
        {"TestRegisterFailsWhenSchemaDocumentUsesUnsupportedSubset",
         TestRegisterFailsWhenSchemaDocumentUsesUnsupportedSubset},
        {"TestRegisterFailsWhenSchemaJsonIsMalformed",
         TestRegisterFailsWhenSchemaJsonIsMalformed},
    };

    int failed = 0;
    for (const auto& [name, test_fn] : tests) {
        try {
            test_fn();
            std::cout << "[PASS] " << name << '\n';
        } catch (const std::exception& ex) {
            ++failed;
            std::cerr << "[FAIL] " << name << ": " << ex.what() << '\n';
        }
    }

    return failed == 0 ? 0 : 1;
}
