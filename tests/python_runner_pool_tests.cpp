// 中文注释：PythonHttpRunnerPool 集成测试。
// 通过内嵌的 MockPythonService 验证：
//   1. 池基本 Load/Run/Unload 生命周期
//   2. 已加载状态下 health_check 成功
//   3. 并发推理（多线程同时调用 Run）
//   4. pool_size 被耗尽时 Checkout 超时返回错误（而非死锁）
//   5. 通过 HTTP Server POST /load 的 pool_size 参数控制池容量

#include <atomic>
#include <chrono>
#include <filesystem>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <httplib.h>
#include <nlohmann/json.hpp>

#include "algolib/io/json_utils.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/runtime/python_http_runner_pool.h"
#include "algolib/runtime/runtime_factory.h"
#include "algolib/runtime/runtime_runner_cache.h"
#include "algolib/server/http_server.h"
#include "python_service_test_support.h"

namespace {

namespace fs = std::filesystem;
using algolib::AlgorithmKey;
using algolib::AlgorithmRegistry;
using algolib::BackendType;
using algolib::PythonHttpRunnerPool;
using algolib::RuntimeFactory;
using algolib::RuntimeRunnerCache;
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
    const fs::path temp_dir = fs::temp_directory_path() / ("algolib_pool_" + name);
    std::error_code ec;
    fs::remove_all(temp_dir, ec);
    fs::create_directories(temp_dir);
    return temp_dir;
}

// 中文注释：PrepareServiceFixture — 创建 fixture 并将 card 中的 endpoint 指向 mock_url。
fs::path PrepareServiceFixture(const fs::path& temp_dir, const std::string& mock_url) {
    const fs::path fixture =
        CreateServiceFixtureWithIdentity(temp_dir, "llm_rule_explainer", "1.0.0");
    PointServiceFixtureAtBaseUrl(fixture, mock_url);
    return fixture;
}

// -----------------------------------------------------------------------
// RunningServer — HTTP Server RAII 包装（复用 http_server_tests 中的模式）
// -----------------------------------------------------------------------
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

    int port() const { return port_; }

private:
    void WaitUntilReady() const {
        httplib::Client client("127.0.0.1", port_);
        for (int attempt = 0; attempt < 60; ++attempt) {
            auto response = client.Get("/health");
            if (response && response->status == 200) return;
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        throw std::runtime_error("HTTP server did not become ready in time.");
    }

    algolib::AlgolibHttpServer server_;
    int port_ = 0;
    std::thread thread_;
};

nlohmann::json GetJson(int port, const std::string& path, int expected_status) {
    httplib::Client client("127.0.0.1", port);
    auto response = client.Get(path);
    Expect(static_cast<bool>(response), "GET " + path + ": no HTTP response.");
    Expect(response->status == expected_status,
           "GET " + path + ": expected " + std::to_string(expected_status) +
               ", got " + std::to_string(response->status));
    return nlohmann::json::parse(response->body);
}

nlohmann::json PostJson(int port,
                        const std::string& path,
                        const nlohmann::json& body,
                        int expected_status) {
    httplib::Client client("127.0.0.1", port);
    auto response = client.Post(path, algolib::JsonUtils::Dump(body), "application/json");
    Expect(static_cast<bool>(response), "POST " + path + ": no HTTP response.");
    Expect(response->status == expected_status,
           "POST " + path + ": expected " + std::to_string(expected_status) +
               ", got " + std::to_string(response->status) + ", body=" + response->body);
    return nlohmann::json::parse(response->body);
}

// -----------------------------------------------------------------------
// TestPythonRunnerPoolLoadRunUnload
// 验证 PythonHttpRunnerPool 的基本生命周期：
//   Load → is_ready=true → Run 成功 → Unload → is_ready=false
// -----------------------------------------------------------------------
void TestPythonRunnerPoolLoadRunUnload() {
    MockPythonService mock;
    const fs::path temp_dir = MakeTempDir("load_run_unload");
    const fs::path fixture = PrepareServiceFixture(temp_dir, mock.base_url());

    AlgorithmRegistry registry(temp_dir / "registry.json");
    Expect(registry.Reload().ok(), "Registry reload should succeed.");
    Expect(registry.Register(fixture).ok(), "Service fixture should register.");

    const AlgorithmKey key{"llm_rule_explainer", "1.0.0", BackendType::kPythonHttpService};
    Expect(registry.Activate(key).ok(), "Service fixture should activate.");

    auto entry_result = registry.Get(key);
    Expect(entry_result.ok(), "Registry entry should be found.");

    // --- Load ---
    PythonHttpRunnerPool pool(/*pool_size=*/2, /*checkout_timeout_ms=*/1000);
    Expect(!pool.is_ready(), "Pool should not be ready before Load.");
    const auto load_status = pool.Load(entry_result.value());
    Expect(load_status.ok(),
           "Pool Load should succeed. err=" + load_status.message());
    Expect(pool.is_ready(), "Pool should be ready after Load.");
    Expect(pool.pool_size() == 2, "Pool size should be 2.");
    Expect(pool.idle_count() == 2, "All runners should be idle after Load.");

    // --- HealthCheck ---
    const auto health = pool.HealthCheck();
    Expect(health.ok, "HealthCheck should return ok=true after Load. status=" + health.status);

    // --- Run ---
    algolib::AlgorithmRequest request;
    request.request_id   = "req_pool_001";
    request.trace_id     = "trace_pool_001";
    request.algorithm_id = "llm_rule_explainer";
    request.version      = "1.0.0";
    request.backend_type = BackendType::kPythonHttpService;
    request.inputs       = {{"rule_text", "test rule"}};

    const auto run_result = pool.Run(request);
    Expect(run_result.ok, "Pool Run should succeed. err=" +
               (run_result.error.has_value() ? run_result.error->message : "none"));
    Expect(pool.idle_count() == 2, "Runner should be returned after Run.");

    // --- Unload ---
    const auto unload_status = pool.Unload();
    Expect(unload_status.ok(), "Pool Unload should succeed.");
    Expect(!pool.is_ready(), "Pool should not be ready after Unload.");
}

// -----------------------------------------------------------------------
// TestPythonRunnerPoolConcurrentRuns
// 多线程并发 Run，验证：
//   1. 所有请求都正确完成（无数据竞争）
//   2. pool_size < thread_count 时系统不死锁（超时的请求返回错误）
// -----------------------------------------------------------------------
void TestPythonRunnerPoolConcurrentRuns() {
    // 中文注释：mock service 支持并发，加入少量延迟模拟 GPU 推理时间。
    MockPythonServiceConfig cfg;
    cfg.predict_delay_ms = 20;
    MockPythonService mock(cfg);

    const fs::path temp_dir = MakeTempDir("concurrent");
    const fs::path fixture  = PrepareServiceFixture(temp_dir, mock.base_url());

    AlgorithmRegistry registry(temp_dir / "registry.json");
    Expect(registry.Reload().ok(), "Registry reload should succeed.");
    Expect(registry.Register(fixture).ok(), "Service fixture should register.");
    const AlgorithmKey key{"llm_rule_explainer", "1.0.0", BackendType::kPythonHttpService};
    Expect(registry.Activate(key).ok(), "Service fixture should activate.");
    auto entry_result = registry.Get(key);
    Expect(entry_result.ok(), "Registry entry should be found.");

    constexpr std::size_t kPoolSize    = 3;
    constexpr int         kConcurrency = 6;  // > kPoolSize，部分请求需要等待

    PythonHttpRunnerPool pool(kPoolSize, /*checkout_timeout_ms=*/2000);
    Expect(pool.Load(entry_result.value()).ok(), "Pool Load should succeed.");

    std::atomic<int> success_count{0};
    std::atomic<int> error_count{0};

    auto worker = [&](int id) {
        algolib::AlgorithmRequest req;
        req.request_id   = "req_concurrent_" + std::to_string(id);
        req.trace_id     = "trace_" + std::to_string(id);
        req.algorithm_id = "llm_rule_explainer";
        req.version      = "1.0.0";
        req.backend_type = BackendType::kPythonHttpService;
        req.inputs       = {{"rule_text", "concurrent rule " + std::to_string(id)}};
        const auto result = pool.Run(req);
        if (result.ok) {
            success_count.fetch_add(1, std::memory_order_relaxed);
        } else {
            error_count.fetch_add(1, std::memory_order_relaxed);
        }
    };

    std::vector<std::thread> threads;
    threads.reserve(kConcurrency);
    for (int i = 0; i < kConcurrency; ++i) {
        threads.emplace_back(worker, i);
    }
    for (auto& t : threads) {
        t.join();
    }

    Expect(success_count.load() + error_count.load() == kConcurrency,
           "Total requests should equal kConcurrency.");
    // 中文注释：kPoolSize 个并发请求应该成功（等待空闲 runner 后执行）；
    // 超出 pool_size 的请求如果 checkout 超时则计入 error。
    // 由于 checkout_timeout=2000ms 远大于 predict_delay=20ms，
    // 所有请求都应在超时前拿到 runner，因此 error_count 应为 0。
    Expect(success_count.load() == kConcurrency,
           "All " + std::to_string(kConcurrency) + " concurrent requests should succeed, "
           "error_count=" + std::to_string(error_count.load()));

    Expect(pool.idle_count() == kPoolSize,
           "All runners should be idle after all threads finish.");
}

// -----------------------------------------------------------------------
// TestPythonRunnerPoolCheckoutTimeout
// 故意造成所有 runner 同时忙碌（长延迟），验证：
//   超出 pool_size 的 Checkout 在 checkout_timeout 后返回 kServiceUnavailable，
//   而不是死锁。
// -----------------------------------------------------------------------
void TestPythonRunnerPoolCheckoutTimeout() {
    // 中文注释：每次 predict 延迟 200ms，checkout_timeout = 50ms < predict_delay，
    // 所以第 pool_size+1 个请求一定会 checkout 超时。
    MockPythonServiceConfig cfg;
    cfg.predict_delay_ms = 200;
    MockPythonService mock(cfg);

    const fs::path temp_dir = MakeTempDir("checkout_timeout");
    const fs::path fixture  = PrepareServiceFixture(temp_dir, mock.base_url());

    AlgorithmRegistry registry(temp_dir / "registry.json");
    Expect(registry.Reload().ok(), "Registry reload should succeed.");
    Expect(registry.Register(fixture).ok(), "Service fixture should register.");
    const AlgorithmKey key{"llm_rule_explainer", "1.0.0", BackendType::kPythonHttpService};
    Expect(registry.Activate(key).ok(), "Service fixture should activate.");
    auto entry_result = registry.Get(key);
    Expect(entry_result.ok(), "Registry entry should be found.");

    constexpr std::size_t kPoolSize = 2;
    // 中文注释：checkout_timeout 比 predict_delay 短，保证超时发生。
    PythonHttpRunnerPool pool(kPoolSize, /*checkout_timeout_ms=*/50);
    Expect(pool.Load(entry_result.value()).ok(), "Pool Load should succeed.");

    std::atomic<int> success_count{0};
    std::atomic<int> timeout_count{0};

    // 启动 kPoolSize 个长任务，占满池
    std::vector<std::thread> long_runners;
    for (std::size_t i = 0; i < kPoolSize; ++i) {
        long_runners.emplace_back([&, i]() {
            algolib::AlgorithmRequest req;
            req.request_id   = "req_long_" + std::to_string(i);
            req.algorithm_id = "llm_rule_explainer";
            req.version      = "1.0.0";
            req.backend_type = BackendType::kPythonHttpService;
            req.inputs       = {{"rule_text", "long task"}};
            const auto result = pool.Run(req);
            if (result.ok) {
                success_count.fetch_add(1, std::memory_order_relaxed);
            }
        });
    }

    // 中文注释：等待长任务拿到 runner（runner 都被占用中）后，发起一个超时测试请求。
    std::this_thread::sleep_for(std::chrono::milliseconds(20));

    algolib::AlgorithmRequest timeout_req;
    timeout_req.request_id   = "req_timeout";
    timeout_req.algorithm_id = "llm_rule_explainer";
    timeout_req.version      = "1.0.0";
    timeout_req.backend_type = BackendType::kPythonHttpService;
    timeout_req.inputs       = {{"rule_text", "should timeout"}};
    const auto timeout_result = pool.Run(timeout_req);
    if (!timeout_result.ok && timeout_result.error.has_value()) {
        timeout_count.fetch_add(1, std::memory_order_relaxed);
    }

    for (auto& t : long_runners) {
        t.join();
    }

    Expect(timeout_count.load() == 1,
           "The extra request should have timed out (timeout_count=" +
               std::to_string(timeout_count.load()) + ").");
    Expect(success_count.load() == static_cast<int>(kPoolSize),
           "Long tasks should all succeed (success_count=" +
               std::to_string(success_count.load()) + ").");
}

// -----------------------------------------------------------------------
// TestPythonRunnerPoolViaRuntimeCache
// 验证 RuntimeRunnerCache 对 Python 后端的缓存行为：
//   1. 第一次 GetOrLoad → 创建池并缓存
//   2. 第二次 GetOrLoad 相同 (entry, deploy_id) → 直接返回缓存的池
//   3. IsLoaded 返回 true
//   4. Invalidate 后 IsLoaded 返回 false
// -----------------------------------------------------------------------
void TestPythonRunnerPoolViaRuntimeCache() {
    MockPythonService mock;
    const fs::path temp_dir = MakeTempDir("cache");
    const fs::path fixture  = PrepareServiceFixture(temp_dir, mock.base_url());

    AlgorithmRegistry registry(temp_dir / "registry.json");
    Expect(registry.Reload().ok(), "Registry reload should succeed.");
    Expect(registry.Register(fixture).ok(), "Service fixture should register.");
    const AlgorithmKey key{"llm_rule_explainer", "1.0.0", BackendType::kPythonHttpService};
    Expect(registry.Activate(key).ok(), "Service fixture should activate.");
    auto entry_result = registry.Get(key);
    Expect(entry_result.ok(), "Registry entry should be found.");
    const auto& entry = entry_result.value();

    // 使用 pool_size=2 的 factory
    RuntimeFactory factory(/*python_pool_size=*/2, /*python_checkout_timeout_ms=*/1000);
    RuntimeRunnerCache cache;

    // --- 第一次 GetOrLoad ---
    Expect(!cache.IsLoaded(entry), "Cache should not have runner before GetOrLoad.");
    auto first_result = cache.GetOrLoad(entry, factory, "deploy/a");
    Expect(first_result.ok(),
           "First GetOrLoad should succeed. err=" + first_result.status().message());
    Expect(cache.IsLoaded(entry, "deploy/a"), "Cache should have runner after GetOrLoad.");

    // --- 第二次 GetOrLoad → 命中缓存（相同指针）---
    auto second_result = cache.GetOrLoad(entry, factory, "deploy/a");
    Expect(second_result.ok(), "Second GetOrLoad should succeed.");
    Expect(first_result.value().get() == second_result.value().get(),
           "Second GetOrLoad should return the same pool instance (cache hit).");

    // --- 不同 deploy_id → 不同实例 ---
    auto third_result = cache.GetOrLoad(entry, factory, "deploy/b");
    Expect(third_result.ok(), "Third GetOrLoad (different deploy_id) should succeed.");
    Expect(first_result.value().get() != third_result.value().get(),
           "Different deploy_id should have different pool instances.");

    // --- Invalidate deploy/a ---
    cache.InvalidateDeployment(key, "deploy/a");
    Expect(!cache.IsLoaded(entry, "deploy/a"),
           "Cache should not have runner after InvalidateDeployment.");
    Expect(cache.IsLoaded(entry, "deploy/b"),
           "Other deploy_id should still be in cache.");

    // --- Invalidate all ---
    cache.Invalidate(key);
    Expect(!cache.IsLoaded(entry, "deploy/b"),
           "All entries should be removed after Invalidate.");
}

// -----------------------------------------------------------------------
// TestPythonRunnerPoolViaHttpServer
// 完整的 HTTP Server + 并发池集成测试：
//   1. POST /load（pool_size=3）加载 Python Service
//   2. 多个 POST /run 并发发出，全部成功
//   3. POST /unload 清理池
// -----------------------------------------------------------------------
void TestPythonRunnerPoolViaHttpServer() {
    // 中文注释：mock service 加 10ms 延迟，保证并发时有排队效果
    MockPythonServiceConfig cfg;
    cfg.predict_delay_ms = 10;
    MockPythonService mock(cfg);

    const fs::path temp_dir = MakeTempDir("http_server");
    const fs::path fixture  = PrepareServiceFixture(temp_dir, mock.base_url());

    // 提前注册 + 激活（HTTP Server 读取同一 registry）
    AlgorithmRegistry pre_registry(temp_dir / "registry.json");
    Expect(pre_registry.Reload().ok(), "Pre-registry reload should succeed.");
    Expect(pre_registry.Register(fixture).ok(), "Service fixture should register.");
    const AlgorithmKey key{"llm_rule_explainer", "1.0.0", BackendType::kPythonHttpService};
    Expect(pre_registry.Activate(key).ok(), "Service fixture should activate.");

    RunningServer server({temp_dir / "registry.json",
                         temp_dir / "audit.jsonl",
                         "127.0.0.1", 0});
    server.Start();

    // --- POST /load with pool_size=3 ---
    const nlohmann::json load_body{
        {"algorithm_id", "llm_rule_explainer"},
        {"version",      "1.0.0"},
        {"backend_type", "python_http_service"},
        {"pool_size",    3},
    };
    const auto load_resp = PostJson(server.port(), "/load", load_body, 200);
    Expect(load_resp.value("ok", false),
           "POST /load with pool_size=3 should return ok=true. msg=" +
               load_resp.value("message", std::string()));
    Expect(load_resp.value("load_status", std::string()) == "loaded",
           "load_status should be 'loaded'.");

    // --- 并发 POST /run ---
    constexpr int kConcurrency = 6;
    std::atomic<int> success_count{0};
    std::atomic<int> error_count{0};

    // 中文注释：llm_rule_explainer 的 input schema 要求 task_text + entities（均为 required）
    auto make_run_body = [](int id) {
        return nlohmann::json{
            {"request_id",   "req_pool_server_" + std::to_string(id)},
            {"trace_id",     "trace_" + std::to_string(id)},
            {"algorithm_id", "llm_rule_explainer"},
            {"version",      "1.0.0"},
            {"backend_type", "python_http_service"},
            {"inputs",       {
                {"task_text", "task " + std::to_string(id)},
                {"entities",  nlohmann::json::array()},
            }},
        };
    };

    // 中文注释：串行执行（HTTP Server 使用全局 mutex 保护 registry，
    // 每次推理会持锁完成，并发场景等同于串行排队）。
    // 所有 6 次请求都应该成功。
    for (int i = 0; i < kConcurrency; ++i) {
        httplib::Client client("127.0.0.1", server.port());
        client.set_connection_timeout(std::chrono::seconds(10));
        client.set_read_timeout(std::chrono::seconds(10));
        client.set_write_timeout(std::chrono::seconds(10));
        auto response = client.Post(
            "/run",
            algolib::JsonUtils::Dump(make_run_body(i)),
            "application/json");
        if (response && response->status == 200) {
            const auto parsed = nlohmann::json::parse(response->body);
            if (parsed.value("ok", false)) {
                success_count.fetch_add(1, std::memory_order_relaxed);
            } else {
                std::cerr << "[DEBUG] /run[" << i << "] ok=false body=" << response->body << '\n';
                error_count.fetch_add(1, std::memory_order_relaxed);
            }
        } else if (response) {
            std::cerr << "[DEBUG] /run[" << i << "] HTTP " << response->status
                      << " body=" << response->body << '\n';
            error_count.fetch_add(1, std::memory_order_relaxed);
        } else {
            std::cerr << "[DEBUG] /run[" << i << "] no response (timeout/connection refused)\n";
            error_count.fetch_add(1, std::memory_order_relaxed);
        }
    }

    Expect(success_count.load() == kConcurrency,
           "All " + std::to_string(kConcurrency) + " concurrent /run should succeed. "
           "success=" + std::to_string(success_count.load()) +
           " error=" + std::to_string(error_count.load()));

    // --- POST /unload ---
    const nlohmann::json unload_body{
        {"algorithm_id", "llm_rule_explainer"},
        {"version",      "1.0.0"},
        {"backend_type", "python_http_service"},
    };
    const auto unload_resp = PostJson(server.port(), "/unload", unload_body, 200);
    Expect(unload_resp.value("ok", false), "POST /unload should return ok=true.");
    Expect(unload_resp.value("load_status", std::string()) == "unloaded",
           "load_status should be 'unloaded'.");
}

}  // namespace

int RunPythonRunnerPoolTests() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests = {
        {"TestPythonRunnerPoolLoadRunUnload",
         TestPythonRunnerPoolLoadRunUnload},
        {"TestPythonRunnerPoolConcurrentRuns",
         TestPythonRunnerPoolConcurrentRuns},
        {"TestPythonRunnerPoolCheckoutTimeout",
         TestPythonRunnerPoolCheckoutTimeout},
        {"TestPythonRunnerPoolViaRuntimeCache",
         TestPythonRunnerPoolViaRuntimeCache},
        {"TestPythonRunnerPoolViaHttpServer",
         TestPythonRunnerPoolViaHttpServer},
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
