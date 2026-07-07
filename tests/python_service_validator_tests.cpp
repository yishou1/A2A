#include <filesystem>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "algolib/validation/algorithm_card_validator.h"
#include "python_service_test_support.h"

namespace {

namespace fs = std::filesystem;
using algolib::AlgorithmCardValidator;
using algolib::ErrorCode;
using algolib::testsupport::CreateServiceFixtureWithIdentity;
using algolib::testsupport::MockPythonService;
using algolib::testsupport::MockPythonServiceConfig;
using algolib::testsupport::PointServiceFixtureAtBaseUrl;
using algolib::testsupport::ReadTextFile;
using algolib::testsupport::ReplaceAll;
using algolib::testsupport::WriteTextFile;

// 中文注释：这里专门覆盖 Phase 3 的远端校验失败分支，确保错误码映射稳定。
void Expect(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

fs::path MakeTempDir(const std::string& name) {
    fs::path temp_dir = fs::temp_directory_path() / ("algolib_python_service_" + name);
    std::error_code ec;
    fs::remove_all(temp_dir, ec);
    fs::create_directories(temp_dir);
    return temp_dir;
}

void ReplaceTimeoutMs(const fs::path& fixture_dir, int timeout_ms) {
    std::string card_content = ReadTextFile(fixture_dir / "algorithm_card.yaml");
    ReplaceAll(&card_content, "    timeout_ms: 10000",
               "    timeout_ms: " + std::to_string(timeout_ms));
    WriteTextFile(fixture_dir / "algorithm_card.yaml", card_content);
}

void TestRegisterFailsWhenHealthEndpointReturns503() {
    fs::path temp_dir = MakeTempDir("health_503");
    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "fixture", "service_health_503", "1.0.0");

    MockPythonServiceConfig config;
    config.algorithm_id = "service_health_503";
    config.version = "1.0.0";
    config.health_http_status = 503;
    MockPythonService mock_service(config);
    PointServiceFixtureAtBaseUrl(fixture_dir, mock_service.base_url());

    AlgorithmCardValidator validator;
    auto result = validator.ValidateFromPath(fixture_dir);
    Expect(!result.ok(), "Validation should fail when /health returns 503.");
    Expect(result.status().code() == ErrorCode::kServiceNotReady,
           "HTTP 503 from /health should map to SERVICE_NOT_READY.");
}

void TestRegisterFailsWhenMetadataDoesNotMatchCard() {
    fs::path temp_dir = MakeTempDir("metadata_mismatch");
    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "fixture", "service_metadata_mismatch",
                                         "1.0.0");

    MockPythonServiceConfig config;
    config.algorithm_id = "service_metadata_mismatch";
    config.version = "1.0.0";
    config.metadata_body = {
        {"algorithm_id", "wrong_algorithm"},
        {"version", "1.0.0"},
        {"backend_type", "python_http_service"},
    };
    MockPythonService mock_service(config);
    PointServiceFixtureAtBaseUrl(fixture_dir, mock_service.base_url());

    AlgorithmCardValidator validator;
    auto result = validator.ValidateFromPath(fixture_dir);
    Expect(!result.ok(), "Validation should fail when /metadata does not match the card.");
    Expect(result.status().code() == ErrorCode::kServiceMetadataMismatch,
           "Metadata mismatch should map to SERVICE_METADATA_MISMATCH.");
}

void TestRegisterFailsWhenPredictReturnsHttp500() {
    fs::path temp_dir = MakeTempDir("predict_http_500");
    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "fixture", "service_predict_http_500",
                                         "1.0.0");

    MockPythonServiceConfig config;
    config.algorithm_id = "service_predict_http_500";
    config.version = "1.0.0";
    config.predict_http_status = 500;
    MockPythonService mock_service(config);
    PointServiceFixtureAtBaseUrl(fixture_dir, mock_service.base_url());

    AlgorithmCardValidator validator;
    auto result = validator.ValidateFromPath(fixture_dir);
    Expect(!result.ok(), "Validation should fail when /predict returns HTTP 500.");
    Expect(result.status().code() == ErrorCode::kServiceHttpError,
           "HTTP 500 from /predict should map to SERVICE_HTTP_ERROR.");
}

void TestRegisterFailsWhenPredictResponseBreaksOutputSchema() {
    fs::path temp_dir = MakeTempDir("predict_bad_output_schema");
    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "fixture",
                                         "service_predict_bad_output_schema", "1.0.0");

    MockPythonServiceConfig config;
    config.algorithm_id = "service_predict_bad_output_schema";
    config.version = "1.0.0";
    config.predict_body = {
        {"ok", true},
        {"request_id", "req_001"},
        {"trace_id", "trace_001"},
        {"algorithm_id", "service_predict_bad_output_schema"},
        {"version", "1.0.0"},
        {"outputs",
         {
             {"explanation", "still looks like success"},
             {"confidence", "not-a-number"},
         }},
        {"usage", {{"latency_ms", 10}}},
        {"error", nullptr},
    };
    MockPythonService mock_service(config);
    PointServiceFixtureAtBaseUrl(fixture_dir, mock_service.base_url());

    AlgorithmCardValidator validator;
    auto result = validator.ValidateFromPath(fixture_dir);
    Expect(!result.ok(), "Validation should fail when response outputs break output.schema.");
    Expect(result.status().code() == ErrorCode::kServiceOutputSchemaInvalid,
           "Bad outputs should map to SERVICE_OUTPUT_SCHEMA_INVALID.");
}

void TestRegisterFailsWhenPredictTimesOut() {
    fs::path temp_dir = MakeTempDir("predict_timeout");
    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "fixture", "service_predict_timeout",
                                         "1.0.0");
    ReplaceTimeoutMs(fixture_dir, 50);

    MockPythonServiceConfig config;
    config.algorithm_id = "service_predict_timeout";
    config.version = "1.0.0";
    config.predict_delay_ms = 200;
    MockPythonService mock_service(config);
    PointServiceFixtureAtBaseUrl(fixture_dir, mock_service.base_url());

    AlgorithmCardValidator validator;
    auto result = validator.ValidateFromPath(fixture_dir);
    Expect(!result.ok(), "Validation should fail when /predict exceeds timeout.");
    Expect(result.status().code() == ErrorCode::kServiceTimeout,
           "Predict timeout should map to SERVICE_TIMEOUT.");
}

}  // namespace

int RunPythonServiceValidatorTests() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"TestRegisterFailsWhenHealthEndpointReturns503",
         TestRegisterFailsWhenHealthEndpointReturns503},
        {"TestRegisterFailsWhenMetadataDoesNotMatchCard",
         TestRegisterFailsWhenMetadataDoesNotMatchCard},
        {"TestRegisterFailsWhenPredictReturnsHttp500",
         TestRegisterFailsWhenPredictReturnsHttp500},
        {"TestRegisterFailsWhenPredictResponseBreaksOutputSchema",
         TestRegisterFailsWhenPredictResponseBreaksOutputSchema},
        {"TestRegisterFailsWhenPredictTimesOut",
         TestRegisterFailsWhenPredictTimesOut},
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
