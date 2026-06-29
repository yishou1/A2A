#include <filesystem>
#include <functional>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/io/json_utils.h"
#include "algolib/io/http_client.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/runtime/execution_coordinator.h"
#include "python_service_test_support.h"

namespace {

namespace fs = std::filesystem;
using algolib::AlgorithmKey;
using algolib::AlgorithmRegistry;
using algolib::BackendType;
using algolib::ErrorCode;
using algolib::ExecutionCoordinator;
using algolib::testsupport::CreateServiceFixtureWithIdentity;
using algolib::testsupport::MockPythonService;
using algolib::testsupport::MockPythonServiceConfig;
using algolib::testsupport::PointServiceFixtureAtBaseUrl;
using algolib::testsupport::SourceRoot;

void Expect(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

fs::path MakeTempDir(const std::string& name) {
    fs::path temp_dir = fs::temp_directory_path() / ("algolib_phase5_" + name);
    std::error_code ec;
    fs::remove_all(temp_dir, ec);
    fs::create_directories(temp_dir);
    return temp_dir;
}

AlgorithmKey OnnxKey() {
    return AlgorithmKey{"onnx_text_classifier", "1.0.0", BackendType::kOnnx};
}

AlgorithmKey ServiceKey(const std::string& algorithm_id = "llm_rule_explainer",
                        const std::string& version = "1.0.0") {
    return AlgorithmKey{algorithm_id, version, BackendType::kPythonHttpService};
}

nlohmann::json ReadLastAuditLog(const fs::path& log_path) {
    std::ifstream input_stream(log_path, std::ios::in | std::ios::binary);
    Expect(input_stream.is_open(), "Audit log should be readable: " + log_path.string());

    std::string line;
    std::string last_line;
    while (std::getline(input_stream, line)) {
        if (!line.empty()) {
            last_line = line;
        }
    }

    Expect(!last_line.empty(), "Audit log should contain at least one record.");
    return nlohmann::json::parse(last_line);
}

void WaitForServiceReady(const std::string& base_url) {
    // 中文注释：同端口重启 mock service 后，短暂等待 /health 就绪，
    // 避免把重启窗口里的连接抖动误判成运行期逻辑失败。
    algolib::HttpClient http_client;
    for (int attempt = 0; attempt < 40; ++attempt) {
        auto health_result = http_client.Get(base_url + "/health", 200);
        if (health_result.ok() && health_result.value().status_code == 200) {
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    throw std::runtime_error("Restarted mock service should become ready before execution.");
}

void RewriteServiceEndpointsInRegistry(const fs::path& registry_path,
                                       const std::string& algorithm_id,
                                       const std::string& version,
                                       const std::string& base_url) {
    // 中文注释：失败路径测试需要把 registry 中记录的服务地址切到新的 mock，
    // 这样能稳定复用“先注册成功、再运行失败”的场景，而不依赖端口复用。
    auto registry_json_result = algolib::JsonUtils::ReadJsonFile(registry_path);
    Expect(registry_json_result.ok(), "Registry JSON should be readable for endpoint rewrite.");

    nlohmann::json registry_json = registry_json_result.value();
    Expect(registry_json.contains("entries") && registry_json.at("entries").is_array(),
           "Registry JSON should contain an entries array.");

    bool updated = false;
    for (auto& entry_json : registry_json.at("entries")) {
        if (!entry_json.is_object()) {
            continue;
        }
        if (entry_json.value("key", nlohmann::json::object())
                .value("algorithm_id", std::string()) != algorithm_id ||
            entry_json.value("key", nlohmann::json::object())
                .value("version", std::string()) != version ||
            entry_json.value("key", nlohmann::json::object())
                .value("backend_type", std::string()) != "python_http_service") {
            continue;
        }

        auto& runtime_json = entry_json["card"]["machine_spec"]["runtime"];
        runtime_json["endpoint"] = base_url + "/predict";
        runtime_json["health_endpoint"] = base_url + "/health";
        runtime_json["metadata_endpoint"] = base_url + "/metadata";
        updated = true;
    }

    Expect(updated, "Registry JSON should contain the target python_http_service entry.");
    Expect(algolib::JsonUtils::WriteJsonFile(registry_path, registry_json).ok(),
           "Registry JSON rewrite should succeed.");
}

void TestRunActiveOnnxAlgorithmWritesSuccessAuditLog() {
    fs::path temp_dir = MakeTempDir("onnx_success");
    fs::path registry_path = temp_dir / "registry.json";
    fs::path log_path = temp_dir / "execution_audit.jsonl";

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed.");
    Expect(registry.Register(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0").ok(),
           "ONNX example should register.");
    Expect(registry.Activate(OnnxKey()).ok(), "ONNX example should activate.");

    ExecutionCoordinator coordinator(registry, log_path);
    algolib::AlgorithmRequest request;
    request.request_id = "req_onnx_001";
    request.trace_id = "trace_onnx_001";
    request.algorithm_id = "onnx_text_classifier";
    request.version = "1.0.0";
    request.backend_type = BackendType::kOnnx;
    request.inputs = {{"text", "Classify this task description"}};

    const auto result = coordinator.Run(request);
    Expect(result.ok, "ONNX run should succeed for active entry.");
    Expect(result.outputs.value("label", std::string()) == "task",
           "ONNX run should return the expected label.");
    Expect(result.usage.contains("latency_ms"),
           "Coordinator should populate latency_ms when runner omits it.");

    const auto audit_log = ReadLastAuditLog(log_path);
    Expect(audit_log.value("status", std::string()) == "success",
           "Audit log should record success status.");
    Expect(audit_log.value("backend_type", std::string()) == "onnx",
           "Audit log should record ONNX backend.");
    Expect(audit_log.value("request_id", std::string()) == "req_onnx_001",
           "Audit log should keep request_id.");
    Expect(audit_log.value("input_hash", std::string()).rfind("sha256:", 0) == 0,
           "Audit log should include SHA-256 input hash.");
}

void TestRunRejectsNonActiveAlgorithm() {
    fs::path temp_dir = MakeTempDir("not_active");
    fs::path registry_path = temp_dir / "registry.json";
    fs::path log_path = temp_dir / "execution_audit.jsonl";

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed.");
    Expect(registry.Register(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0").ok(),
           "ONNX example should register.");

    ExecutionCoordinator coordinator(registry, log_path);
    algolib::AlgorithmRequest request;
    request.request_id = "req_not_active";
    request.trace_id = "trace_not_active";
    request.algorithm_id = "onnx_text_classifier";
    request.version = "1.0.0";
    request.backend_type = BackendType::kOnnx;
    request.inputs = {{"text", "Classify this task description"}};

    const auto result = coordinator.Run(request);
    Expect(!result.ok, "Non-active algorithm should not be runnable.");
    Expect(result.error.has_value(), "Failure result should contain an error.");
    Expect(result.error->code == "ALGORITHM_NOT_ACTIVE",
           "Failure result should map to ALGORITHM_NOT_ACTIVE.");

    const auto audit_log = ReadLastAuditLog(log_path);
    Expect(audit_log.value("status", std::string()) == "failure",
           "Audit log should record failure status.");
    Expect(audit_log.value("error_code", std::string()) == "ALGORITHM_NOT_ACTIVE",
           "Audit log should record ALGORITHM_NOT_ACTIVE.");
}

void TestRunActivePythonServiceAlgorithm() {
    fs::path temp_dir = MakeTempDir("python_success");
    fs::path registry_path = temp_dir / "registry.json";
    fs::path log_path = temp_dir / "execution_audit.jsonl";

    MockPythonService mock_service;
    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "fixture", "llm_rule_explainer", "1.0.0");
    PointServiceFixtureAtBaseUrl(fixture_dir, mock_service.base_url());

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed.");
    Expect(registry.Register(fixture_dir).ok(), "Service fixture should register.");
    Expect(registry.Activate(ServiceKey()).ok(), "Service fixture should activate.");

    ExecutionCoordinator coordinator(registry, log_path);
    algolib::AlgorithmRequest request;
    request.request_id = "req_service_001";
    request.trace_id = "trace_service_001";
    request.algorithm_id = "llm_rule_explainer";
    request.version = "1.0.0";
    request.backend_type = BackendType::kPythonHttpService;
    request.inputs = {
        {"task_text", "Check whether this task follows the rule."},
        {"entities", nlohmann::json::array()},
    };

    const auto result = coordinator.Run(request);
    Expect(result.ok, "Python service run should succeed.");
    Expect(result.outputs.contains("explanation"),
           "Python service result should contain explanation.");
    Expect(result.usage.value("latency_ms", 0) == 120,
           "Service usage should be preserved when present.");

    const auto audit_log = ReadLastAuditLog(log_path);
    Expect(audit_log.value("backend_type", std::string()) == "python_http_service",
           "Audit log should record service backend.");
    Expect(audit_log.value("status", std::string()) == "success",
           "Audit log should record success for service run.");
}

void TestRunReturnsBackendFailureDirectly() {
    fs::path temp_dir = MakeTempDir("python_failure");
    fs::path registry_path = temp_dir / "registry.json";
    fs::path log_path = temp_dir / "execution_audit.jsonl";

    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "fixture", "llm_rule_explainer", "1.0.0");
    {
        MockPythonService registration_service;
        PointServiceFixtureAtBaseUrl(fixture_dir, registration_service.base_url());

        AlgorithmRegistry registry(registry_path);
        Expect(registry.Reload().ok(), "Reload should succeed.");
        Expect(registry.Register(fixture_dir).ok(),
               "Service fixture should register successfully before runtime failure is injected.");
        Expect(registry.Activate(ServiceKey()).ok(), "Service fixture should activate.");
    }

    MockPythonServiceConfig failure_config;
    failure_config.predict_http_status = 500;
    MockPythonService failure_service(failure_config);
    WaitForServiceReady(failure_service.base_url());
    RewriteServiceEndpointsInRegistry(
        registry_path, "llm_rule_explainer", "1.0.0", failure_service.base_url());

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed.");

    algolib::AlgorithmRequest request;
    request.request_id = "req_service_fail";
    request.trace_id = "trace_service_fail";
    request.algorithm_id = "llm_rule_explainer";
    request.version = "1.0.0";
    request.backend_type = BackendType::kPythonHttpService;
    request.inputs = {
        {"task_text", "Check whether this task follows the rule."},
        {"entities", nlohmann::json::array()},
    };

    algolib::HttpClient http_client;
    auto direct_predict_result = http_client.PostJson(
        failure_service.base_url() + "/predict", algolib::ToJson(request), 1000);
    Expect(direct_predict_result.ok(),
           "Direct failure mock call should succeed at the transport layer.");
    Expect(direct_predict_result.value().status_code == 500,
           "Failure mock should return HTTP 500 for predict.");

    ExecutionCoordinator coordinator(registry, log_path);
    const auto result = coordinator.Run(request);
    Expect(!result.ok, "Service backend failure should be returned directly.");
    Expect(result.error.has_value(), "Failure result should contain backend error.");
    Expect(result.error->code == "SERVICE_HTTP_ERROR",
           "HTTP 500 should map to SERVICE_HTTP_ERROR.");

    const auto audit_log = ReadLastAuditLog(log_path);
    Expect(audit_log.value("status", std::string()) == "failure",
           "Audit log should record failure.");
    Expect(audit_log.value("error_code", std::string()) == "SERVICE_HTTP_ERROR",
           "Audit log should keep backend failure code.");
}

}  // namespace

int RunPhase5ExecutionTests() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"TestRunActiveOnnxAlgorithmWritesSuccessAuditLog",
         TestRunActiveOnnxAlgorithmWritesSuccessAuditLog},
        {"TestRunRejectsNonActiveAlgorithm", TestRunRejectsNonActiveAlgorithm},
        {"TestRunActivePythonServiceAlgorithm", TestRunActivePythonServiceAlgorithm},
        {"TestRunReturnsBackendFailureDirectly", TestRunReturnsBackendFailureDirectly},
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
