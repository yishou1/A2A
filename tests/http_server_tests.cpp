#include <chrono>
#include <filesystem>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <httplib.h>
#include <nlohmann/json.hpp>

#include "algolib/io/json_utils.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/server/http_server.h"
#include "python_service_test_support.h"

namespace {

namespace fs = std::filesystem;
using algolib::AlgorithmKey;
using algolib::AlgorithmRegistry;
using algolib::BackendType;
using algolib::testsupport::SourceRoot;

void Expect(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

fs::path MakeTempDir(const std::string& name) {
    const fs::path temp_dir = fs::temp_directory_path() / ("algolib_http_server_" + name);
    std::error_code ec;
    fs::remove_all(temp_dir, ec);
    fs::create_directories(temp_dir);
    return temp_dir;
}

AlgorithmKey OnnxKey() {
    return AlgorithmKey{"onnx_text_classifier", "1.0.0", BackendType::kOnnx};
}

void PrepareActiveOnnxRegistry(const fs::path& registry_path) {
    AlgorithmRegistry registry(registry_path);
    Expect(registry.Reload().ok(), "Registry reload should succeed.");
    Expect(registry.Register(SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0").ok(),
           "ONNX example should register.");
    Expect(registry.Activate(OnnxKey()).ok(), "ONNX example should activate.");
}

class RunningServer {
public:
    explicit RunningServer(algolib::HttpServerConfig config) : server_(std::move(config)) {}

    ~RunningServer() {
        server_.Stop();
        if (thread_.joinable()) {
            thread_.join();
        }
    }

    void Start() {
        port_ = server_.BindToAnyPort("127.0.0.1");
        Expect(port_ > 0, "HTTP server should bind to an ephemeral port.");
        thread_ = std::thread([this]() { server_.ListenAfterBind(); });
        WaitUntilReady();
    }

    int port() const {
        return port_;
    }

private:
    void WaitUntilReady() const {
        // 中文注释：测试里轮询 /health，避免 server 线程刚启动时客户端抢跑导致偶发失败。
        httplib::Client client("127.0.0.1", port_);
        for (int attempt = 0; attempt < 60; ++attempt) {
            auto response = client.Get("/health");
            if (response && response->status == 200) {
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        throw std::runtime_error("HTTP server did not become ready in time.");
    }

    algolib::AlgolibHttpServer server_;
    int port_ = 0;
    std::thread thread_;
};

nlohmann::json ParseResponse(const httplib::Result& response,
                             int expected_status,
                             const std::string& message) {
    Expect(static_cast<bool>(response), message + ": no HTTP response.");
    Expect(response->status == expected_status,
           message + ": unexpected HTTP status " + std::to_string(response->status) +
               ", body=" + response->body);
    return nlohmann::json::parse(response->body);
}

nlohmann::json GetJson(int port, const std::string& path, int expected_status) {
    httplib::Client client("127.0.0.1", port);
    return ParseResponse(client.Get(path), expected_status, "GET " + path);
}

nlohmann::json PostJson(int port,
                        const std::string& path,
                        const nlohmann::json& body,
                        int expected_status) {
    httplib::Client client("127.0.0.1", port);
    return ParseResponse(client.Post(path, algolib::JsonUtils::Dump(body), "application/json"),
                         expected_status, "POST " + path);
}

nlohmann::json DeleteJson(int port, const std::string& path, int expected_status) {
    httplib::Client client("127.0.0.1", port);
    return ParseResponse(client.Delete(path), expected_status, "DELETE " + path);
}

void TestHttpServerListsShowsAndRunsActiveOnnxAlgorithm() {
    const fs::path temp_dir = MakeTempDir("run_onnx");
    const fs::path registry_path = temp_dir / "registry.json";
    const fs::path log_path = temp_dir / "execution_audit.jsonl";
    PrepareActiveOnnxRegistry(registry_path);

    RunningServer server({registry_path, log_path, "127.0.0.1", 0});
    server.Start();

    const auto health = GetJson(server.port(), "/health", 200);
    Expect(health.value("ok", false), "Health endpoint should return ok=true.");
    Expect(health.value("runner_cache_size", -1) == 0,
           "Runner cache should be empty before the first /run request.");

    const auto algorithms = GetJson(server.port(), "/algorithms", 200);
    Expect(algorithms.value("count", 0) == 1, "Active algorithm list should contain one entry.");
    Expect(algorithms.at("algorithms").at(0).value("algorithm_id", std::string()) ==
               "onnx_text_classifier",
           "Agent list should expose the ONNX algorithm.");

    const auto card =
        GetJson(server.port(), "/algorithms/onnx_text_classifier/1.0.0/onnx", 200);
    Expect(card.value("ok", false), "show-card endpoint should return ok=true.");
    Expect(card.at("entry").value("status", std::string()) == "active",
           "show-card endpoint should include active status.");
    Expect(card.at("agent_view").contains("performance"),
           "Agent view should include performance metadata.");

    const nlohmann::json request{
        {"request_id", "req_http_onnx_001"},
        {"trace_id", "trace_http_onnx_001"},
        {"algorithm_id", "onnx_text_classifier"},
        {"version", "1.0.0"},
        {"backend_type", "onnx"},
        {"inputs", {{"text", "Classify this task text."}}},
    };
    const auto run = PostJson(server.port(), "/run", request, 200);
    Expect(run.value("ok", false), "HTTP /run should succeed for active ONNX algorithm.");
    Expect(run.at("outputs").value("label", std::string()) == "task",
           "HTTP /run should return ONNX output payload.");

    const auto health_after_first_run = GetJson(server.port(), "/health", 200);
    Expect(health_after_first_run.value("runner_cache_size", 0) == 1,
           "HTTP server should cache the loaded ONNX runner after /run.");

    const auto second_run = PostJson(server.port(), "/run", request, 200);
    Expect(second_run.value("ok", false), "Second HTTP /run should reuse cached runner.");
    const auto health_after_second_run = GetJson(server.port(), "/health", 200);
    Expect(health_after_second_run.value("runner_cache_size", 0) == 1,
           "Runner cache should keep one ONNX entry after repeated runs.");
}

void TestHttpServerLifecycleEndpointsManageRegistry() {
    const fs::path temp_dir = MakeTempDir("lifecycle");
    const fs::path registry_path = temp_dir / "registry.json";
    const fs::path log_path = temp_dir / "execution_audit.jsonl";

    RunningServer server({registry_path, log_path, "127.0.0.1", 0});
    server.Start();

    const nlohmann::json register_body{
        {"package_or_card_path",
         (SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0").generic_string()},
    };
    const auto registered =
        PostJson(server.port(), "/algorithms/register", register_body, 201);
    Expect(registered.value("status", std::string()) == "validated",
           "Register endpoint should create a validated entry.");

    const auto all_algorithms = GetJson(server.port(), "/algorithms?active_only=false", 200);
    Expect(all_algorithms.value("count", 0) == 1,
           "Inactive algorithm should appear when active_only=false.");

    const auto activated =
        PostJson(server.port(), "/algorithms/onnx_text_classifier/1.0.0/onnx/activate",
                 nlohmann::json::object(), 200);
    Expect(activated.value("status", std::string()) == "active",
           "Activate endpoint should mark entry active.");

    const auto deleted =
        DeleteJson(server.port(), "/algorithms/onnx_text_classifier/1.0.0/onnx", 200);
    Expect(deleted.value("status", std::string()) == "deleted",
           "Delete endpoint should mark entry deleted.");

    const auto after_delete = GetJson(server.port(), "/algorithms?active_only=false", 200);
    Expect(after_delete.value("count", 0) == 0,
           "Deleted entries should stay hidden from agent list.");
}

}  // namespace

int RunHttpServerTests() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"TestHttpServerListsShowsAndRunsActiveOnnxAlgorithm",
         TestHttpServerListsShowsAndRunsActiveOnnxAlgorithm},
        {"TestHttpServerLifecycleEndpointsManageRegistry",
         TestHttpServerLifecycleEndpointsManageRegistry},
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
