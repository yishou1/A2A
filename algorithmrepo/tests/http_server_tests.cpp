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

    const auto function_catalog =
        GetJson(server.port(), "/operational-functions", 200);
    Expect(function_catalog.value("count", 0) == 28,
           "Operational function catalog endpoint should expose all 28 functions.");

    const auto algorithms = GetJson(server.port(), "/algorithms", 200);
    Expect(algorithms.value("count", 0) == 1, "Active algorithm list should contain one entry.");
    Expect(algorithms.at("algorithms").at(0).value("algorithm_id", std::string()) ==
               "onnx_text_classifier",
           "Agent list should expose the ONNX algorithm.");
    Expect(algorithms.at("algorithms").at(0).value("registry_status", std::string()) ==
               "active",
           "Algorithm list should expose the registry lifecycle status.");
    Expect(algorithms.at("algorithms").at(0).value("card_status", std::string()) ==
               "draft",
           "Algorithm list should expose the algorithm card declaration status.");

    const auto input_schema = GetJson(
        server.port(),
        "/algorithms/onnx_text_classifier/1.0.0/onnx/schemas/input", 200);
    Expect(input_schema.value("type", std::string()) == "object",
           "Input schema endpoint should return the complete JSON Schema.");
    Expect(input_schema.at("properties").at("text").value("type", std::string()) ==
               "string",
           "Input schema endpoint should preserve property definitions.");

    const auto output_schema = GetJson(
        server.port(),
        "/algorithms/onnx_text_classifier/1.0.0/onnx/schemas/output", 200);
    Expect(output_schema.at("properties").contains("label"),
           "Output schema endpoint should return output property definitions.");

    const auto card =
        GetJson(server.port(), "/algorithms/onnx_text_classifier/1.0.0/onnx", 200);
    Expect(card.value("ok", false), "show-card endpoint should return ok=true.");
    Expect(card.at("entry").value("status", std::string()) == "active",
           "show-card endpoint should include active status.");
    Expect(card.at("agent_view").contains("performance"),
           "Agent view should include performance metadata.");
    Expect(card.at("agent_view").at("operational_functions").size() == 1,
           "Agent view should expose operational function mappings.");

    const auto by_function =
        GetJson(server.port(), "/algorithms?function_code=classify", 200);
    Expect(by_function.value("count", 0) == 1,
           "Algorithm discovery should support function_code filtering.");

    const nlohmann::json request{
        {"request_id", "req_http_onnx_001"},
        {"trace_id", "trace_http_onnx_001"},
        {"algorithm_id", "onnx_text_classifier"},
        {"version", "1.0.0"},
        {"backend_type", "onnx"},
        {"function_context",
         {{"function_id", "KC-06"},
          {"function_code", "classify"},
          {"workflow_instance_id", "workflow-http-001"},
          {"step_instance_id", "step-http-006"}}},
        {"inputs", {{"text", "Classify this task text."}}},
    };
    const auto run = PostJson(server.port(), "/run", request, 200);
    Expect(run.value("ok", false), "HTTP /run should succeed for active ONNX algorithm.");
    Expect(run.at("outputs").value("label", std::string()) == "task",
           "HTTP /run should return ONNX output payload.");
    Expect(run.at("function_execution").value("function_id", std::string()) == "KC-06",
           "HTTP /run should return executed function telemetry.");

    const auto trace = GetJson(
        server.port(), "/traces/trace_http_onnx_001/function-executions", 200);
    Expect(trace.value("count", 0) == 1,
           "Trace endpoint should return the first function execution.");
    Expect(trace.at("function_executions").at(0).value("algorithm_id", std::string()) ==
               "onnx_text_classifier",
           "Trace endpoint should identify the executed algorithm.");
    Expect(trace.at("function_executions").at(0).at("function_execution")
               .value("function_code", std::string()) == "classify",
           "Trace endpoint should expose the executed function code.");

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

    const nlohmann::json deployment{
        {"deploy_id", "node-01/deploy-01"},
        {"node_id", "node-01"},
        {"deploy_status", "unloaded"},
    };
    const auto deployment_added = PostJson(
        server.port(),
        "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments",
        deployment, 201);
    Expect(deployment_added.at("entry").at("deployments").size() == 1,
           "Deployment endpoint should accept the documented node/deploy ID format.");
    const auto deployment_removed = DeleteJson(
        server.port(),
        "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments/node-01%2Fdeploy-01",
        200);
    Expect(deployment_removed.at("entry").at("deployments").empty(),
           "Deployment endpoint should delete a URL-encoded node/deploy ID.");

    const auto deleted =
        DeleteJson(server.port(), "/algorithms/onnx_text_classifier/1.0.0/onnx", 200);
    Expect(deleted.value("status", std::string()) == "deleted",
           "Delete endpoint should mark entry deleted.");

    const auto after_delete = GetJson(server.port(), "/algorithms?active_only=false", 200);
    Expect(after_delete.value("count", 0) == 0,
           "Deleted entries should stay hidden from agent list.");
}

// ---------------------------------------------------------------------------
// http_pull 集成测试
// ---------------------------------------------------------------------------

// 中文注释：TestHttpPullFileEndpoint
// 验证 GET /algorithms/{id}/{version}/{backend}/files/{relative_path} 端点：
//   1. 正常文件可以下载并内容一致
//   2. 路径穿越（含 ".."）被拒绝（400）
//   3. 不存在的文件返回 404
void TestHttpPullFileEndpoint() {
    const fs::path temp_dir = MakeTempDir("http_pull_files");
    const fs::path registry_path = temp_dir / "registry.json";
    const fs::path log_path = temp_dir / "execution_audit.jsonl";
    PrepareActiveOnnxRegistry(registry_path);

    RunningServer server({registry_path, log_path, "127.0.0.1", 0});
    server.Start();

    // --- 正常下载 model.onnx ---
    {
        httplib::Client client("127.0.0.1", server.port());
        auto response = client.Get(
            "/algorithms/onnx_text_classifier/1.0.0/onnx/files/model.onnx");
        Expect(static_cast<bool>(response),
               "File download: no HTTP response for model.onnx.");
        Expect(response->status == 200,
               "File download: expected 200, got " +
                   std::to_string(response->status) + " for model.onnx.");
        Expect(!response->body.empty(),
               "File download: model.onnx body should not be empty.");

        // 与本地原始文件对比大小
        const fs::path original =
            SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0" / "model.onnx";
        Expect(fs::exists(original),
               "Fixture model.onnx should exist in source tree.");
        const auto original_size = fs::file_size(original);
        Expect(response->body.size() == original_size,
               "Downloaded model.onnx size mismatch: expected " +
                   std::to_string(original_size) + ", got " +
                   std::to_string(response->body.size()) + ".");
    }

    // --- 下载文本配置文件 preprocess.yaml ---
    {
        httplib::Client client("127.0.0.1", server.port());
        auto response = client.Get(
            "/algorithms/onnx_text_classifier/1.0.0/onnx/files/preprocess.yaml");
        Expect(static_cast<bool>(response),
               "File download: no HTTP response for preprocess.yaml.");
        Expect(response->status == 200,
               "File download: expected 200 for preprocess.yaml.");
        Expect(!response->body.empty(),
               "File download: preprocess.yaml body should not be empty.");
    }

    // --- 路径穿越攻击被拒绝 ---
    {
        httplib::Client client("127.0.0.1", server.port());
        auto response = client.Get(
            "/algorithms/onnx_text_classifier/1.0.0/onnx/files/../../../etc/passwd");
        Expect(static_cast<bool>(response),
               "Path traversal: no HTTP response.");
        Expect(response->status == 400,
               "Path traversal attack should return 400, got " +
                   std::to_string(response->status) + ".");
    }

    // --- 不存在的文件返回 404 ---
    {
        httplib::Client client("127.0.0.1", server.port());
        auto response = client.Get(
            "/algorithms/onnx_text_classifier/1.0.0/onnx/files/nonexistent.bin");
        Expect(static_cast<bool>(response),
               "Missing file: no HTTP response.");
        Expect(response->status == 404,
               "Missing file should return 404, got " +
                   std::to_string(response->status) + ".");
    }
}

// 中文注释：TestHttpPullLoadEndpointNoneMode
// 验证 POST /load 端点在默认 transfer_mode=none 下的行为：
//   1. 加载成功后 load_status="loaded"，health.ok=true
//   2. 再次加载同一模型返回 load_status="already_loaded"
//   3. POST /unload 后缓存被清除
void TestHttpPullLoadEndpointNoneMode() {
    const fs::path temp_dir = MakeTempDir("http_pull_load_none");
    const fs::path registry_path = temp_dir / "registry.json";
    const fs::path log_path = temp_dir / "execution_audit.jsonl";
    PrepareActiveOnnxRegistry(registry_path);

    RunningServer server({registry_path, log_path, "127.0.0.1", 0});
    server.Start();

    // 先注册一个 deployment
    const nlohmann::json deploy_spec{
        {"deploy_id",    "local/d1"},
        {"node_id",      "local"},
        {"endpoint",     ""},
        {"deploy_status","unloaded"},
    };
    const auto deployed = PostJson(
        server.port(),
        "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments",
        deploy_spec, 201);
    Expect(deployed.value("ok", false),
           "Adding deployment should return ok=true.");

    // 第一次加载（transfer_mode 默认 none）
    const nlohmann::json load_body{
        {"algorithm_id", "onnx_text_classifier"},
        {"version",      "1.0.0"},
        {"backend_type", "onnx"},
        {"deploy_id",    "local/d1"},
    };
    const auto first_load = PostJson(server.port(), "/load", load_body, 200);
    Expect(first_load.value("ok", false),
           "/load (none mode) should return ok=true.");
    Expect(first_load.value("load_status", std::string()) == "loaded",
           "/load first time should return load_status=loaded, got: " +
               first_load.value("load_status", std::string()));
    Expect(first_load.contains("health") &&
               first_load.at("health").value("ok", false),
           "/load should return health.ok=true after successful load.");

    // 验证部署状态已变为 ready
    const auto entry_after = GetJson(
        server.port(), "/algorithms/onnx_text_classifier/1.0.0/onnx", 200);
    Expect(entry_after.value("ok", false), "show-card after load should return ok.");
    const auto& deps = entry_after.at("entry").at("deployments");
    Expect(!deps.empty(), "Entry should have at least one deployment.");
    Expect(deps.at(0).value("deploy_status", std::string()) == "ready",
           "deploy_status should be 'ready' after load, got: " +
               deps.at(0).value("deploy_status", std::string()));

    // 第二次加载同一模型 → already_loaded
    const auto second_load = PostJson(server.port(), "/load", load_body, 200);
    Expect(second_load.value("ok", false),
           "/load second time should still return ok=true.");
    Expect(second_load.value("load_status", std::string()) == "already_loaded",
           "/load second time should return load_status=already_loaded, got: " +
               second_load.value("load_status", std::string()));

    // 卸载
    const auto unload = PostJson(server.port(), "/unload", load_body, 200);
    Expect(unload.value("ok", false), "/unload should return ok=true.");
    Expect(unload.value("load_status", std::string()) == "unloaded",
           "/unload should return load_status=unloaded.");

    // 卸载后再加载应回到 loaded（不是 already_loaded）
    const auto reload = PostJson(server.port(), "/load", load_body, 200);
    Expect(reload.value("load_status", std::string()) == "loaded",
           "After unload, reload should return load_status=loaded.");
}

// 中文注释：TestHttpPullEndToEnd
// 完整 http_pull 流程验证：
//   1. 启动"主库"服务器（server_A，持有真实模型文件）
//   2. 启动"节点"服务器（server_B，registry 为空，无本地模型文件）
//   3. Agent 向 server_B 的 /load 发送 transfer_mode=http_pull
//   4. server_B 从 server_A 的 /files 端点拉取文件到 /tmp
//   5. server_B 成功加载 ONNX session，/run 推理正常
void TestHttpPullEndToEnd() {
    // --- 准备主库（server_A）：有真实模型文件 ---
    const fs::path dir_a = MakeTempDir("http_pull_e2e_src");
    const fs::path registry_a = dir_a / "registry.json";
    PrepareActiveOnnxRegistry(registry_a);

    RunningServer server_a({registry_a, dir_a / "audit_a.jsonl", "127.0.0.1", 0});
    server_a.Start();

    // --- 准备节点（server_B）：registry 中有注册条目，但无本地 .onnx 文件 ---
    // server_B 使用不同的 registry 目录（但 package_root 指向同一 examples 目录，
    // 仅用于注册条目查找；实际模型文件通过 http_pull 下载到临时目录）
    const fs::path dir_b = MakeTempDir("http_pull_e2e_dst");
    const fs::path registry_b = dir_b / "registry.json";
    PrepareActiveOnnxRegistry(registry_b);  // 复用同一个注册辅助（有模型信息）

    RunningServer server_b({registry_b, dir_b / "audit_b.jsonl", "127.0.0.1", 0});
    server_b.Start();

    // 1. Agent 注册 deployment（指定节点 server_B）
    const nlohmann::json deploy_spec{
        {"deploy_id",    "node-b/d1"},
        {"node_id",      "node-b"},
        {"endpoint",     "http://127.0.0.1:" + std::to_string(server_b.port())},
        {"deploy_status","unloaded"},
    };
    const auto deploy_resp = PostJson(
        server_b.port(),
        "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments",
        deploy_spec, 201);
    Expect(deploy_resp.value("ok", false), "E2E: deploy registration should succeed.");

    // 2. Agent 向 server_B 发出 http_pull 加载指令
    const std::string source_url =
        "http://127.0.0.1:" + std::to_string(server_a.port());
    const nlohmann::json load_body{
        {"algorithm_id",    "onnx_text_classifier"},
        {"version",         "1.0.0"},
        {"backend_type",    "onnx"},
        {"deploy_id",       "node-b/d1"},
        {"transfer_mode",   "http_pull"},
        {"source_base_url", source_url},
        {"target_local_dir",
         (dir_b / "pulled_models").generic_string()},
    };
    const auto load_resp = PostJson(server_b.port(), "/load", load_body, 200);
    Expect(load_resp.value("ok", false),
           "E2E: http_pull load should return ok=true. message=" +
               load_resp.value("message", std::string()));
    Expect(load_resp.value("load_status", std::string()) == "loaded",
           "E2E: load_status should be 'loaded', got: " +
               load_resp.value("load_status", std::string()));
    Expect(load_resp.contains("health") &&
               load_resp.at("health").value("ok", false),
           "E2E: health should be ok after http_pull load.");

    // 3. 验证文件已被下载到目标目录
    const fs::path pulled_model =
        dir_b / "pulled_models" / "model.onnx";
    Expect(fs::exists(pulled_model),
           "E2E: pulled model.onnx should exist at " + pulled_model.string());
    const fs::path original_model =
        SourceRoot() / "examples" / "onnx_text_classifier" / "1.0.0" / "model.onnx";
    Expect(fs::file_size(pulled_model) == fs::file_size(original_model),
           "E2E: pulled model.onnx size should match original.");

    // 4. 通过 deploy_id 向 server_B 推理，验证模型已正确加载
    const nlohmann::json run_body{
        {"request_id",   "req_e2e_pull_001"},
        {"trace_id",     "trace_e2e_001"},
        {"algorithm_id", "onnx_text_classifier"},
        {"version",      "1.0.0"},
        {"backend_type", "onnx"},
        {"deploy_id",    "node-b/d1"},
        {"inputs",       {{"text", "Classify this task text."}}},
    };
    const auto run_resp = PostJson(server_b.port(), "/run", run_body, 200);
    Expect(run_resp.value("ok", false),
           "E2E: /run via deploy_id should succeed after http_pull load.");
    Expect(run_resp.at("outputs").value("label", std::string()) == "task",
           "E2E: /run output label should be 'task'.");

    // 5. 验证 deploy_status、local_model_path 及 updated_at 均已持久化
    const auto entry = GetJson(
        server_b.port(), "/algorithms/onnx_text_classifier/1.0.0/onnx", 200);
    const auto& deps = entry.at("entry").at("deployments");
    bool found_ready = false;
    std::string local_model_path_in_registry;
    std::string updated_at_in_registry;
    std::string deployed_at_in_registry;
    for (const auto& d : deps) {
        if (d.value("deploy_id", std::string()) == "node-b/d1") {
            found_ready = (d.value("deploy_status", std::string()) == "ready");
            local_model_path_in_registry = d.value("local_model_path", std::string());
            updated_at_in_registry       = d.value("updated_at", std::string());
            deployed_at_in_registry      = d.value("deployed_at", std::string());
            break;
        }
    }
    Expect(found_ready,
           "E2E: deployment node-b/d1 should have deploy_status=ready.");

    // 验证 local_model_path 已被持久化为拉取到的本地 model.onnx 路径
    const fs::path expected_model_path =
        dir_b / "pulled_models" / "model.onnx";
    Expect(!local_model_path_in_registry.empty(),
           "E2E: local_model_path should be persisted in registry after http_pull.");
    Expect(local_model_path_in_registry == expected_model_path.generic_string(),
           "E2E: local_model_path should point to pulled model. "
           "expected=" + expected_model_path.generic_string() +
           " got=" + local_model_path_in_registry);

    // 验证 updated_at 已被自动填入（非空 ISO 8601 格式）
    Expect(!updated_at_in_registry.empty(),
           "E2E: updated_at should be auto-filled after status transition.");
    Expect(updated_at_in_registry.size() >= 16,
           "E2E: updated_at should be a valid ISO 8601 timestamp, got: " +
               updated_at_in_registry);

    // 验证 deployed_at 在 AddDeployment 时已被自动填入（非空）
    Expect(!deployed_at_in_registry.empty(),
           "E2E: deployed_at should be auto-filled by AddDeployment.");

    // 6. 验证注册表重载后 local_model_path 仍然保留（持久化正确）
    const auto reload_resp = PostJson(server_b.port(), "/reload", {}, 200);
    Expect(reload_resp.value("ok", false), "E2E: /reload should return ok=true.");

    const auto entry_after_reload = GetJson(
        server_b.port(), "/algorithms/onnx_text_classifier/1.0.0/onnx", 200);
    const auto& deps_after = entry_after_reload.at("entry").at("deployments");
    std::string path_after_reload;
    for (const auto& d : deps_after) {
        if (d.value("deploy_id", std::string()) == "node-b/d1") {
            path_after_reload = d.value("local_model_path", std::string());
            break;
        }
    }
    Expect(path_after_reload == expected_model_path.generic_string(),
           "E2E: local_model_path should survive registry reload. "
           "expected=" + expected_model_path.generic_string() +
           " got=" + path_after_reload);
}

}  // namespace

int RunHttpServerTests() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"TestHttpServerListsShowsAndRunsActiveOnnxAlgorithm",
         TestHttpServerListsShowsAndRunsActiveOnnxAlgorithm},
        {"TestHttpServerLifecycleEndpointsManageRegistry",
         TestHttpServerLifecycleEndpointsManageRegistry},
        {"TestHttpPullFileEndpoint",
         TestHttpPullFileEndpoint},
        {"TestHttpPullLoadEndpointNoneMode",
         TestHttpPullLoadEndpointNoneMode},
        {"TestHttpPullEndToEnd",
         TestHttpPullEndToEnd},
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
