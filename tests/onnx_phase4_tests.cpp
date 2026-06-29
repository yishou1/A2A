#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "algolib/core/algorithm_entry.h"
#include "algolib/runtime/onnx_runner.h"
#include "algolib/validation/algorithm_card_validator.h"

namespace {

namespace fs = std::filesystem;
using algolib::AlgorithmCard;
using algolib::AlgorithmEntry;
using algolib::AlgorithmRequest;
using algolib::AlgorithmStatus;
using algolib::BackendType;
using algolib::ErrorCode;
using algolib::OnnxRunner;
using algolib::PostprocessConfig;
using algolib::PreprocessConfig;

// 中文注释：Phase 4 这里同时覆盖黑盒注册验证和白盒 runner 行为。
void Expect(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

fs::path SourceRoot() {
    return fs::path(ALGOLIB_SOURCE_DIR);
}

fs::path MakeTempDir(const std::string& name) {
    fs::path temp_dir = fs::temp_directory_path() / ("algolib_onnx_phase4_" + name);
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

void CopyFixtureFile(const fs::path& source_path, const fs::path& target_path) {
    fs::create_directories(target_path.parent_path());
    std::error_code ec;
    fs::copy_file(source_path, target_path, fs::copy_options::overwrite_existing, ec);
    Expect(!ec, "Test fixture file should be copyable: " + source_path.string());
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

fs::path CreateOnnxFixture(const fs::path& temp_dir) {
    const fs::path source_dir =
        SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0";
    const fs::path target_dir = temp_dir / "onnx_text_classifier" / "1.0.0";
    fs::create_directories(target_dir);
    fs::copy(source_dir, target_dir,
             fs::copy_options::recursive | fs::copy_options::overwrite_existing);
    return target_dir;
}

AlgorithmEntry BuildTensorRoundTripEntry(const fs::path& package_root) {
    AlgorithmEntry entry;
    entry.key.algorithm_id = "tensor_round_trip";
    entry.key.version = "1.0.0";
    entry.key.backend_type = BackendType::kOnnx;
    entry.status = AlgorithmStatus::kDraft;
    entry.package_root = package_root;
    entry.card_path = package_root / "algorithm_card.yaml";

    AlgorithmCard card;
    card.algorithm_id = "tensor_round_trip";
    card.version = "1.0.0";
    card.display_name = "Tensor Round Trip";
    card.backend_type = BackendType::kOnnx;
    card.status = AlgorithmStatus::kDraft;
    card.task_family = "test";
    card.machine_spec.runtime.backend_type = BackendType::kOnnx;
    card.machine_spec.runtime.model_uri = "model.onnx";
    card.machine_spec.runtime.execution_provider = "cpu";
    card.machine_spec.preprocess = algolib::ProcessSpec{"preprocess.yaml", ""};
    card.machine_spec.postprocess = algolib::ProcessSpec{"postprocess.yaml", ""};
    entry.card = card;
    return entry;
}

void TestOnnxPackageValidationSucceedsForExample() {
    algolib::AlgorithmCardValidator validator;
    auto result =
        validator.ValidateFromPath(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0");
    Expect(result.ok(), "Example ONNX package should pass Phase 4 validation.");
}

void TestOnnxValidationFailsOnUnsupportedPreprocessType() {
    fs::path fixture_dir = CreateOnnxFixture(MakeTempDir("bad_preprocess"));
    WriteTextFile(fixture_dir / "preprocess.yaml", "type: unsupported_preprocess\n");

    algolib::AlgorithmCardValidator validator;
    auto result = validator.ValidateFromPath(fixture_dir);
    Expect(!result.ok(), "Unsupported preprocess type should fail validation.");
    Expect(result.status().code() == ErrorCode::kPreprocessFailed,
           "Unsupported preprocess type should map to PREPROCESS_FAILED.");
}

void TestOnnxValidationFailsWhenGoldenCaseDoesNotMatch() {
    fs::path fixture_dir = CreateOnnxFixture(MakeTempDir("golden_mismatch"));
    std::string expected_json = ReadTextFile(fixture_dir / "golden_cases" / "case_001_expected.json");
    ReplaceAll(&expected_json, "\"task\"", "\"report\"");
    WriteTextFile(fixture_dir / "golden_cases" / "case_001_expected.json", expected_json);

    algolib::AlgorithmCardValidator validator;
    auto result = validator.ValidateFromPath(fixture_dir);
    Expect(!result.ok(), "Golden case mismatch should fail validation.");
    Expect(result.status().code() == ErrorCode::kGoldenCaseFailed,
           "Golden case mismatch should map to GOLDEN_CASE_FAILED.");
}

void TestOnnxValidationFailsOnUnsupportedExecutionProvider() {
    fs::path fixture_dir = CreateOnnxFixture(MakeTempDir("bad_execution_provider"));
    std::string card_yaml = ReadTextFile(fixture_dir / "algorithm_card.yaml");
    ReplaceAll(&card_yaml, "    execution_provider: cpu", "    execution_provider: cuda");
    WriteTextFile(fixture_dir / "algorithm_card.yaml", card_yaml);

    algolib::AlgorithmCardValidator validator;
    auto result = validator.ValidateFromPath(fixture_dir);
    Expect(!result.ok(), "Unsupported execution provider should fail validation.");
    Expect(result.status().code() == ErrorCode::kOnnxLoadFailed,
           "Unsupported execution provider should map to ONNX_LOAD_FAILED.");
}

void TestOnnxRunnerSupportsTensorFromJsonAndNoOpPostprocess() {
    fs::path temp_dir = MakeTempDir("tensor_round_trip");
    CopyFixtureFile(SourceRoot() / "tests" / "fixtures" / "onnx_identity_vector.onnx",
                    temp_dir / "model.onnx");
    WriteTextFile(temp_dir / "preprocess.yaml", "type: tensor_from_json\n");
    WriteTextFile(temp_dir / "postprocess.yaml", "type: no_op\n");

    AlgorithmEntry entry = BuildTensorRoundTripEntry(temp_dir);
    OnnxRunner runner;
    auto load_status = runner.Load(entry);
    Expect(load_status.ok(), "Tensor round-trip entry should load.");

    AlgorithmRequest request;
    request.request_id = "req_tensor";
    request.trace_id = "trace_tensor";
    request.algorithm_id = "tensor_round_trip";
    request.version = "1.0.0";
    request.backend_type = BackendType::kOnnx;
    request.inputs = nlohmann::json::array({1, 2, 3});

    const auto result = runner.Run(request);
    Expect(result.ok, "Tensor round-trip run should succeed.");
    Expect(result.outputs == nlohmann::json::array({1, 2, 3}),
           "no_op postprocess should return the session output unchanged.");
}

}  // namespace

int RunOnnxPhase4Tests() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"TestOnnxPackageValidationSucceedsForExample",
         TestOnnxPackageValidationSucceedsForExample},
        {"TestOnnxValidationFailsOnUnsupportedPreprocessType",
         TestOnnxValidationFailsOnUnsupportedPreprocessType},
        {"TestOnnxValidationFailsWhenGoldenCaseDoesNotMatch",
         TestOnnxValidationFailsWhenGoldenCaseDoesNotMatch},
        {"TestOnnxValidationFailsOnUnsupportedExecutionProvider",
         TestOnnxValidationFailsOnUnsupportedExecutionProvider},
        {"TestOnnxRunnerSupportsTensorFromJsonAndNoOpPostprocess",
         TestOnnxRunnerSupportsTensorFromJsonAndNoOpPostprocess},
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
