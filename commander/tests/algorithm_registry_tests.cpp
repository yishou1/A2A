#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "algolib/registry/algorithm_registry.h"
#include "python_service_test_support.h"

namespace {

namespace fs = std::filesystem;
using algolib::AlgorithmKey;
using algolib::AlgorithmRegistry;
using algolib::AlgorithmStatus;
using algolib::BackendType;
using algolib::ErrorCode;
using algolib::testsupport::MockPythonService;
using algolib::testsupport::MockPythonServiceConfig;

void Expect(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

fs::path SourceRoot() {
    return fs::path(ALGOLIB_SOURCE_DIR);
}

fs::path MakeTempDir(const std::string& name) {
    fs::path temp_dir = fs::temp_directory_path() / ("algolib_" + name);
    std::error_code ec;
    fs::remove_all(temp_dir, ec);
    fs::create_directories(temp_dir);
    return temp_dir;
}

std::string ReadTextFile(const fs::path& file_path) {
    std::ifstream input_stream(file_path, std::ios::in | std::ios::binary);
    Expect(input_stream.is_open(), "Test fixture file should be readable: " + file_path.string());
    return std::string(std::istreambuf_iterator<char>(input_stream),
                       std::istreambuf_iterator<char>());
}

void WriteTextFile(const fs::path& file_path, const std::string& content) {
    fs::create_directories(file_path.parent_path());
    std::ofstream output_stream(file_path, std::ios::out | std::ios::binary | std::ios::trunc);
    Expect(output_stream.is_open(),
           "Test fixture file should be writable: " + file_path.string());
    output_stream << content;
}

void ReplaceAll(std::string* content,
                const std::string& from,
                const std::string& to) {
    std::size_t start_pos = 0;
    while ((start_pos = content->find(from, start_pos)) != std::string::npos) {
        content->replace(start_pos, from.length(), to);
        start_pos += to.length();
    }
}

void PointServiceFixtureAtBaseUrl(const fs::path& fixture_dir,
                                  const std::string& base_url) {
    std::string card_content = ReadTextFile(fixture_dir / "algorithm_card.yaml");
    ReplaceAll(&card_content,
               "endpoint: http://localhost:8080/predict",
               "endpoint: " + base_url + "/predict");
    ReplaceAll(&card_content,
               "health_endpoint: http://localhost:8080/health",
               "health_endpoint: " + base_url + "/health");
    ReplaceAll(&card_content,
               "metadata_endpoint: http://localhost:8080/metadata",
               "metadata_endpoint: " + base_url + "/metadata");
    WriteTextFile(fixture_dir / "algorithm_card.yaml", card_content);
}

fs::path CreateServiceFixtureWithIdentity(const fs::path& temp_dir,
                                          const std::string& algorithm_id,
                                          const std::string& version) {
    const fs::path source_dir =
        SourceRoot() / "examples" / "python_http_service_llm_explainer" / "1.0.0";
    const fs::path target_dir = temp_dir / algorithm_id / version;
    fs::create_directories(target_dir);
    fs::copy(source_dir, target_dir,
             fs::copy_options::recursive | fs::copy_options::overwrite_existing);

    std::string card_content = ReadTextFile(target_dir / "algorithm_card.yaml");
    ReplaceAll(&card_content, "algorithm_id: llm_rule_explainer",
               "algorithm_id: " + algorithm_id);
    ReplaceAll(&card_content, "version: 1.0.0", "version: " + version);
    WriteTextFile(target_dir / "algorithm_card.yaml", card_content);

    std::string request_content =
        ReadTextFile(target_dir / "golden_cases" / "case_001_request.json");
    ReplaceAll(&request_content, "\"algorithm_id\": \"llm_rule_explainer\"",
               "\"algorithm_id\": \"" + algorithm_id + "\"");
    ReplaceAll(&request_content, "\"version\": \"1.0.0\"",
               "\"version\": \"" + version + "\"");
    WriteTextFile(target_dir / "golden_cases" / "case_001_request.json", request_content);

    std::string response_content =
        ReadTextFile(target_dir / "golden_cases" / "case_001_response.json");
    ReplaceAll(&response_content, "\"algorithm_id\": \"llm_rule_explainer\"",
               "\"algorithm_id\": \"" + algorithm_id + "\"");
    ReplaceAll(&response_content, "\"version\": \"1.0.0\"",
               "\"version\": \"" + version + "\"");
    WriteTextFile(target_dir / "golden_cases" / "case_001_response.json", response_content);

    return target_dir;
}

std::string BuildFileUri(const fs::path& path) {
    return "file:///" + path.lexically_normal().generic_string();
}

AlgorithmKey OnnxKey() {
    return AlgorithmKey{"onnx_text_classifier", "1.0.0", BackendType::kOnnx};
}

AlgorithmKey ServiceKey() {
    return AlgorithmKey{"llm_rule_explainer", "1.0.0",
                        BackendType::kPythonHttpService};
}

void TestRegisterAndPersistOnnxAlgorithm() {
    fs::path temp_dir = MakeTempDir("register_and_persist");
    fs::path registry_path = temp_dir / "registry.json";

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for an empty registry.");

    auto register_result =
        registry.Register(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0");
    Expect(register_result.ok(), "ONNX example should register successfully.");
    Expect(register_result.value().status == AlgorithmStatus::kValidated,
           "Register should store the entry as validated.");
    Expect(register_result.value().key == OnnxKey(),
           "Registered ONNX entry should match the expected key.");

    auto list_result = registry.List();
    Expect(list_result.size() == 1, "Registry list should contain one entry.");
    Expect(list_result.front().input_schema_summary["properties"].size() == 1,
           "Input schema summary should expose one input property.");

    AlgorithmRegistry reloaded_registry(registry_path);
    Expect(reloaded_registry.Reload().ok(), "Reload after persistence should succeed.");
    auto get_result = reloaded_registry.Get(OnnxKey());
    Expect(get_result.ok(), "Reloaded registry should find the persisted ONNX entry.");
    Expect(get_result.value().card.display_name == "ONNX Text Classifier",
           "Persisted entry should keep the original display_name.");
    Expect(get_result.value().card.performance.has_value(),
           "Persisted entry should keep the performance block.");
    Expect(get_result.value().card.performance->time_complexity == "O(n)",
           "Persisted entry should keep the time complexity field.");
    Expect(get_result.value().card.resource_requirements.has_value(),
           "Persisted entry should keep the resource requirements block.");
    Expect(get_result.value().card.resource_requirements->recommended_memory_mb == 1024,
           "Persisted entry should keep recommended memory metadata.");
    Expect(get_result.value().card.model_profile.has_value(),
           "Persisted entry should keep the model profile block.");
    Expect(get_result.value().card.model_profile->parameter_count_text == "120K",
           "Persisted entry should keep human-readable parameter count.");
}

void TestServiceLifecycleAndAgentView() {
    fs::path temp_dir = MakeTempDir("service_lifecycle");
    fs::path registry_path = temp_dir / "registry.json";
    MockPythonService mock_service;
    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "fixture", "llm_rule_explainer", "1.0.0");
    PointServiceFixtureAtBaseUrl(fixture_dir, mock_service.base_url());

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for service registry.");

    auto register_result = registry.Register(fixture_dir);
    Expect(register_result.ok(),
           "Python HTTP Service example should register successfully. actual=" +
               register_result.status().ToString());
    Expect(register_result.value().status == AlgorithmStatus::kValidated,
           "Service entry should start as validated.");

    auto validate_result = registry.Validate(ServiceKey());
    Expect(validate_result.ok(),
           "Explicit validate should succeed on existing service entry. actual=" +
               validate_result.status().ToString());
    Expect(validate_result.value().status == AlgorithmStatus::kValidated,
           "Validate should keep validated status.");

    auto activate_result = registry.Activate(ServiceKey());
    Expect(activate_result.ok(), "Activate should succeed.");
    Expect(activate_result.value().status == AlgorithmStatus::kActive,
           "Service entry should become active.");

    const auto active_views = registry.ListAgentViews(true);
    Expect(active_views.size() == 1, "One active entry should appear in the agent view.");
    Expect(active_views.front()["algorithm_id"] == "llm_rule_explainer",
           "Agent view should expose the service algorithm_id.");
    Expect(active_views.front()["agent_card"]["summary"].is_string(),
           "Agent view should contain a summary.");
    Expect(active_views.front()["performance"]["time_complexity"] == "O(n + s)",
           "Agent view should expose time complexity for the service algorithm.");
    Expect(active_views.front()["performance"]["space_complexity"] == "O(n + s)",
           "Agent view should expose space complexity for the service algorithm.");
    Expect(active_views.front()["performance"]["complexity_variable"].is_string(),
           "Agent view should expose the complexity variable description.");
    Expect(active_views.front()["resource_requirements"]["recommended_memory_mb"] == 8192,
           "Agent view should expose recommended memory requirements.");
    Expect(active_views.front()["resource_requirements"]["gpu_type"] == "optional",
           "Agent view should expose GPU type requirements.");
    Expect(active_views.front()["model_profile"]["parameter_count_text"] == "7B",
           "Agent view should expose human-readable parameter count.");
    Expect(active_views.front()["model_profile"]["precision"] == "fp16",
           "Agent view should expose model precision.");

    auto disable_result = registry.Disable(ServiceKey());
    Expect(disable_result.ok(), "Disable should succeed.");
    Expect(disable_result.value().status == AlgorithmStatus::kDisabled,
           "Service entry should become disabled.");
    Expect(registry.ListAgentViews(true).empty(),
           "Disabled entries should not appear in the active agent view.");

    auto delete_result = registry.Delete(ServiceKey());
    Expect(delete_result.ok(), "Delete should succeed.");
    Expect(delete_result.value().status == AlgorithmStatus::kDeleted,
           "Service entry should become deleted.");
    Expect(registry.List().empty(), "Deleted entries should be hidden from list().");
}

void TestDuplicateRegisterIsRejected() {
    fs::path temp_dir = MakeTempDir("duplicate_register");
    fs::path registry_path = temp_dir / "registry.json";

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for duplicate test.");

    auto first_register =
        registry.Register(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0");
    Expect(first_register.ok(), "First register should succeed.");

    auto second_register =
        registry.Register(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0");
    Expect(!second_register.ok(), "Second register should fail on duplicate key.");
    Expect(second_register.status().code() == ErrorCode::kRegistryConflict,
           "Duplicate register should return REGISTRY_CONFLICT.");
}

void TestSameIdVersionDifferentBackendCanCoexist() {
    fs::path temp_dir = MakeTempDir("same_id_different_backend");
    fs::path registry_path = temp_dir / "registry.json";

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for coexist test.");

    auto onnx_register =
        registry.Register(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0");
    Expect(onnx_register.ok(), "ONNX fixture should register successfully.");

    fs::path service_fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "service_fixture",
                                         "onnx_text_classifier", "1.0.0");
    MockPythonServiceConfig service_config;
    service_config.algorithm_id = "onnx_text_classifier";
    service_config.version = "1.0.0";
    MockPythonService mock_service(service_config);
    PointServiceFixtureAtBaseUrl(service_fixture_dir, mock_service.base_url());
    auto service_register = registry.Register(service_fixture_dir / "algorithm_card.yaml");
    Expect(service_register.ok(),
           "Service fixture with the same id/version but different backend should register.");

    const auto entries = registry.List();
    Expect(entries.size() == 2,
           "Registry should keep both entries when backend_type differs.");

    auto onnx_result = registry.Get(OnnxKey());
    Expect(onnx_result.ok(), "ONNX key should still resolve.");
    auto service_result = registry.Get(
        AlgorithmKey{"onnx_text_classifier", "1.0.0", BackendType::kPythonHttpService});
    Expect(service_result.ok(), "Python HTTP Service key should resolve independently.");
}

void TestRegisterFromFileUriAndDeletedTransitionsFail() {
    fs::path temp_dir = MakeTempDir("file_uri_and_deleted");
    fs::path registry_path = temp_dir / "registry.json";

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for file URI test.");

    const std::string file_uri = BuildFileUri(
        SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0");
    auto register_result = registry.Register(file_uri);
    Expect(register_result.ok(), "Register should accept a file URI path.");

    auto delete_result = registry.Delete(OnnxKey());
    Expect(delete_result.ok(), "Delete should succeed before invalid transition checks.");

    auto validate_result = registry.Validate(OnnxKey());
    Expect(!validate_result.ok(), "Deleted entries should not be re-validated.");
    Expect(validate_result.status().code() == ErrorCode::kStatusTransitionInvalid,
           "Re-validating a deleted entry should return STATUS_TRANSITION_INVALID.");

    auto activate_result = registry.Activate(OnnxKey());
    Expect(!activate_result.ok(), "Deleted entries should not be activated.");
    Expect(activate_result.status().code() == ErrorCode::kStatusTransitionInvalid,
           "Activating a deleted entry should return STATUS_TRANSITION_INVALID.");
}

void TestInvalidCardReturnsMissingRequiredField() {
    fs::path temp_dir = MakeTempDir("invalid_card_missing_field");
    fs::path registry_path = temp_dir / "registry.json";
    fs::path fixture_dir =
        CreateServiceFixtureWithIdentity(temp_dir / "broken_fixture",
                                         "broken_rule_explainer", "1.0.0");

    std::string card_content = ReadTextFile(fixture_dir / "algorithm_card.yaml");
    ReplaceAll(&card_content,
               "    timeout_ms: 10000\n",
               "");
    WriteTextFile(fixture_dir / "algorithm_card.yaml", card_content);

    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Reload should succeed for invalid card test.");

    auto register_result = registry.Register(fixture_dir / "algorithm_card.yaml");
    Expect(!register_result.ok(), "Register should fail for an invalid algorithm card.");
    Expect(register_result.status().code() == ErrorCode::kMissingRequiredField,
           "Missing timeout_ms should map to MISSING_REQUIRED_FIELD.");
}

}  // namespace

int RunAlgorithmRegistryTests() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"TestRegisterAndPersistOnnxAlgorithm", TestRegisterAndPersistOnnxAlgorithm},
        {"TestServiceLifecycleAndAgentView", TestServiceLifecycleAndAgentView},
        {"TestDuplicateRegisterIsRejected", TestDuplicateRegisterIsRejected},
        {"TestSameIdVersionDifferentBackendCanCoexist",
         TestSameIdVersionDifferentBackendCanCoexist},
        {"TestRegisterFromFileUriAndDeletedTransitionsFail",
         TestRegisterFromFileUriAndDeletedTransitionsFail},
        {"TestInvalidCardReturnsMissingRequiredField",
         TestInvalidCardReturnsMissingRequiredField},
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
