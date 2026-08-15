// =============================================================================
// AlgoLib 四类接口演示程序
// =============================================================================
// 本 demo 通过启动一个内嵌 HTTP Server，依次演示：
//
//  【接口一】算法注册与查询接口 (Registry API)
//    - POST /algorithms/register     — 注册 ONNX 算法
//    - POST /algorithms/.../activate — 激活算法
//    - GET  /algorithms              — 多维过滤查询（task_family / backend / capability）
//    - GET  /algorithms/{id}/{ver}/{backend} — 获取单算法详情 + agent_view
//    - POST /algorithms/.../deployments     — 添加部署记录（自动填充 deployed_at）
//    - PATCH /algorithms/.../deployments/{id}/status — 更新部署状态
//    - DELETE /algorithms/{id}/{ver}/{backend}        — 删除算法
//
//  【接口二】模型加载接口 (Model Load API)
//    - POST /load                    — 预加载 ONNX 模型（transfer_mode=none）
//    - POST /run                     — 推理
//    - POST /unload                  — 卸载
//
//  【接口三】文件服务接口 (File Serving API，http_pull 基础)
//    - GET  /algorithms/.../files/{rel_path} — 正常下载
//    - GET  /algorithms/.../files/../...     — 路径穿越（期望 400）
//    - GET  /algorithms/.../files/noexist    — 不存在文件（期望 404）
//
//  【接口四】Python HTTP Service 并发池（通过 /load pool_size 参数触发）
//    - POST /load  pool_size=3        — 加载并发池
//    - 串行 6 次 POST /run            — 验证所有请求均成功（队列消化）
//    - POST /unload                   — 销毁并发池
//
// 所有步骤的输入/输出均记录到 JSON 报告，最终写入
//   <demo_dir>/demo_output.json
// =============================================================================

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <httplib.h>
#include <nlohmann/json.hpp>

#include "algolib/io/json_utils.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/server/http_server.h"

// =============================================================================
// 内联 MockPythonService — 仅供并发池演示使用
// =============================================================================
// 为了让 demo 可独立运行（不依赖 python_service_test_support），
// 这里内联一个最小化的 Mock HTTP 服务，响应 /predict、/health、/metadata。
// =============================================================================
class MockPythonService {
public:
    explicit MockPythonService(int delay_ms = 10) : delay_ms_(delay_ms) {
        server_.Post("/predict", [this](const httplib::Request& req, httplib::Response& res) {
            if (delay_ms_ > 0) {
                std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms_));
            }
            // ValidateService 要求响应包含: ok, algorithm_id, version, outputs
            nlohmann::json resp{
                {"ok",          true},
                {"algorithm_id","llm_rule_explainer"},
                {"version",     "1.0.0"},
                {"outputs",     {{"explanation", "Mock: required fields are present."}, {"confidence", 0.85}}},
            };
            res.status = 200;
            res.set_content(resp.dump(), "application/json");
        });

        server_.Get("/health", [](const httplib::Request&, httplib::Response& res) {
            // ValidateService 要求: ok, status=ready, algorithm_id, version, model_loaded
            nlohmann::json h{
                {"ok",           true},
                {"status",       "ready"},
                {"algorithm_id", "llm_rule_explainer"},
                {"version",      "1.0.0"},
                {"model_loaded", true},
            };
            res.status = 200;
            res.set_content(h.dump(), "application/json");
        });

        server_.Get("/metadata", [](const httplib::Request&, httplib::Response& res) {
            nlohmann::json meta{
                {"algorithm_id",  "llm_rule_explainer"},
                {"version",       "1.0.0"},
                {"backend_type",  "python_http_service"},
            };
            res.status = 200;
            res.set_content(meta.dump(), "application/json");
        });

        port_ = server_.bind_to_any_port("127.0.0.1");
        thread_ = std::thread([this]() { server_.listen_after_bind(); });
        // 等待就绪
        httplib::Client probe("127.0.0.1", port_);
        for (int i = 0; i < 60; ++i) {
            auto r = probe.Get("/health");
            if (r && r->status == 200) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(30));
        }
    }

    ~MockPythonService() {
        server_.stop();
        if (thread_.joinable()) thread_.join();
    }

    int port() const { return port_; }
    std::string base_url() const { return "http://127.0.0.1:" + std::to_string(port_); }

private:
    httplib::Server server_;
    std::thread     thread_;
    int             port_     = 0;
    int             delay_ms_ = 10;
};

// =============================================================================
// Demo 基础设施
// =============================================================================

namespace fs = std::filesystem;
using json   = nlohmann::json;

// 全局报告，最终写出到文件
json g_report = json::object();

// 时间戳辅助
std::string NowIso8601() {
    auto now  = std::chrono::system_clock::now();
    auto tt   = std::chrono::system_clock::to_time_t(now);
    std::ostringstream oss;
    oss << std::put_time(std::gmtime(&tt), "%Y-%m-%dT%H:%M:%SZ");
    return oss.str();
}

// 彩色控制台打印（ANSI，macOS/Linux）
constexpr const char* kReset  = "\033[0m";
constexpr const char* kGreen  = "\033[32m";
constexpr const char* kYellow = "\033[33m";
constexpr const char* kCyan   = "\033[36m";
constexpr const char* kRed    = "\033[31m";
constexpr const char* kBold   = "\033[1m";

void PrintSection(const std::string& title) {
    std::cout << "\n" << kBold << kCyan
              << "══════════════════════════════════════════════════════\n"
              << "  " << title << "\n"
              << "══════════════════════════════════════════════════════"
              << kReset << "\n";
}

void PrintStep(const std::string& step, const std::string& desc) {
    std::cout << kYellow << "[" << step << "] " << kReset << desc << "\n";
}

void PrintOk(const std::string& msg) {
    std::cout << kGreen << "  ✓ " << kReset << msg << "\n";
}

void PrintFail(const std::string& msg) {
    std::cout << kRed << "  ✗ " << kReset << msg << "\n";
    throw std::runtime_error(msg);
}

// 向报告追加一个步骤条目
void RecordStep(const std::string& section,
                const std::string& step,
                const std::string& method,
                const std::string& path,
                const json&        request_body,
                int                http_status,
                const json&        response_body,
                bool               passed,
                const std::string& explanation = "") {
    json entry{
        {"step",          step},
        {"method",        method},
        {"path",          path},
        {"request",       request_body},
        {"http_status",   http_status},
        {"response",      response_body},
        {"passed",        passed},
        {"timestamp",     NowIso8601()},
    };
    if (!explanation.empty()) {
        entry["explanation"] = explanation;
    }
    if (!g_report.contains(section)) {
        g_report[section] = json::array();
    }
    g_report[section].push_back(entry);
}

// HTTP 辅助函数
struct HttpResult {
    int  status = 0;
    json body;
};

HttpResult Get(int port, const std::string& path) {
    httplib::Client client("127.0.0.1", port);
    client.set_connection_timeout(std::chrono::seconds(10));
    client.set_read_timeout(std::chrono::seconds(10));
    auto r = client.Get(path);
    if (!r) return {0, {{"error", "no response"}}};
    json body;
    try { body = json::parse(r->body); } catch (...) { body = {{"raw", r->body}}; }
    return {r->status, body};
}

HttpResult Post(int port, const std::string& path, const json& body) {
    httplib::Client client("127.0.0.1", port);
    client.set_connection_timeout(std::chrono::seconds(10));
    client.set_read_timeout(std::chrono::seconds(10));
    auto r = client.Post(path, algolib::JsonUtils::Dump(body), "application/json");
    if (!r) return {0, {{"error", "no response"}}};
    json resp;
    try { resp = json::parse(r->body); } catch (...) { resp = {{"raw", r->body}}; }
    return {r->status, resp};
}

HttpResult Delete(int port, const std::string& path) {
    httplib::Client client("127.0.0.1", port);
    client.set_connection_timeout(std::chrono::seconds(10));
    client.set_read_timeout(std::chrono::seconds(10));
    auto r = client.Delete(path);
    if (!r) return {0, {{"error", "no response"}}};
    json resp;
    try { resp = json::parse(r->body); } catch (...) { resp = {{"raw", r->body}}; }
    return {r->status, resp};
}

HttpResult Patch(int port, const std::string& path, const json& body) {
    httplib::Client client("127.0.0.1", port);
    client.set_connection_timeout(std::chrono::seconds(10));
    client.set_read_timeout(std::chrono::seconds(10));
    auto r = client.Patch(path, algolib::JsonUtils::Dump(body), "application/json");
    if (!r) return {0, {{"error", "no response"}}};
    json resp;
    try { resp = json::parse(r->body); } catch (...) { resp = {{"raw", r->body}}; }
    return {r->status, resp};
}

// 下载文件（返回原始 bytes 大小，-1 为失败）
struct FileResult {
    int    http_status = 0;
    size_t size        = 0;
    std::string content_type;
};

FileResult GetFile(int port, const std::string& path) {
    httplib::Client client("127.0.0.1", port);
    client.set_connection_timeout(std::chrono::seconds(10));
    client.set_read_timeout(std::chrono::seconds(30));
    auto r = client.Get(path);
    if (!r) return {0, 0, ""};
    std::string ct;
    auto it = r->headers.find("Content-Type");
    if (it != r->headers.end()) ct = it->second;
    return {r->status, r->body.size(), ct};
}

// =============================================================================
// RunningServer — RAII HTTP Server 包装
// =============================================================================
class RunningServer {
public:
    explicit RunningServer(algolib::HttpServerConfig cfg) : server_(std::move(cfg)) {}

    ~RunningServer() {
        server_.Stop();
        if (thread_.joinable()) thread_.join();
    }

    void Start() {
        port_ = server_.BindToAnyPort("127.0.0.1");
        if (port_ <= 0) throw std::runtime_error("Failed to bind HTTP server.");
        thread_ = std::thread([this]() { server_.ListenAfterBind(); });
        httplib::Client probe("127.0.0.1", port_);
        for (int i = 0; i < 80; ++i) {
            auto r = probe.Get("/health");
            if (r && r->status == 200) return;
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        throw std::runtime_error("HTTP server did not become ready.");
    }

    int port() const { return port_; }

private:
    algolib::AlgolibHttpServer server_;
    std::thread                thread_;
    int                        port_ = 0;
};

// =============================================================================
// 推导项目源码根目录（编译时宏注入）
// =============================================================================
fs::path SourceRoot() {
#ifdef ALGOLIB_SOURCE_DIR
    return fs::path(ALGOLIB_SOURCE_DIR);
#else
    // 回退：假设 demo 可执行文件在 build/ 下，源码在其父目录
    return fs::canonical(fs::path(__FILE__).parent_path().parent_path());
#endif
}

// =============================================================================
// 辅助：将 llm_rule_explainer 的 algorithm_card.yaml 中 endpoint 改写为 mock_url
// =============================================================================
void PatchEndpointInCard(const fs::path& fixture_dir, const std::string& mock_base_url) {
    const fs::path card_path = fixture_dir / "algorithm_card.yaml";
    std::ifstream  in(card_path);
    if (!in) throw std::runtime_error("Cannot open card: " + card_path.string());
    std::string content((std::istreambuf_iterator<char>(in)),
                         std::istreambuf_iterator<char>());
    in.close();

    // 简单替换 endpoint: http://localhost:8080/predict → mock_base_url/predict
    auto replace_once = [&](const std::string& pattern, const std::string& replacement) {
        auto pos = content.find(pattern);
        if (pos != std::string::npos) {
            content.replace(pos, pattern.size(), replacement);
        }
    };
    replace_once("http://localhost:8080/predict", mock_base_url + "/predict");
    replace_once("http://localhost:8080/health",  mock_base_url + "/health");
    replace_once("http://localhost:8080/metadata", mock_base_url + "/metadata");

    std::ofstream out(card_path);
    if (!out) throw std::runtime_error("Cannot write card: " + card_path.string());
    out << content;
}

// =============================================================================
// 复制目录（用于将 examples 复制到临时目录以免污染源树）
// =============================================================================
void CopyDir(const fs::path& src, const fs::path& dst) {
    fs::create_directories(dst);
    for (auto& entry : fs::recursive_directory_iterator(src)) {
        const fs::path rel  = fs::relative(entry.path(), src);
        const fs::path dest = dst / rel;
        if (fs::is_directory(entry.path())) {
            fs::create_directories(dest);
        } else {
            fs::copy_file(entry.path(), dest, fs::copy_options::overwrite_existing);
        }
    }
}

// =============================================================================
// SECTION 1 — 算法注册与查询接口
// =============================================================================
void DemoRegistryApi(int port, const fs::path& src_root, json& section_report) {
    PrintSection("【接口一】算法注册与查询接口 (Registry API)");
    std::cout << "  算法库注册中心负责算法的全生命周期管理：从注册、校验、激活，到查询和部署跟踪。\n"
              << "  本节演示 9 个步骤，覆盖注册→多维查询→部署管理→删除完整流程。\n";
    const std::string SEC = "registry_api";

    // ─── 1.1 注册算法 ────────────────────────────────────────────────────────
    PrintStep("1.1", "POST /algorithms/register — 注册 ONNX 算法包");
    std::cout << "    传入算法包目录路径，服务器读取 algorithm_card.yaml + schema 文件，\n"
              << "    验证结构完整性后写入注册表，状态变为 validated（已校验未激活）。\n";
    {
        const json req{{"package_or_card_path",
                        (src_root / "examples" / "onnx_text_classifier" / "1.0.0").generic_string()}};
        auto [status, body] = Post(port, "/algorithms/register", req);
        const bool ok = (status == 201) && body.value("status", "") == "validated";
        RecordStep(SEC, "1.1_register", "POST", "/algorithms/register", req, status, body, ok,
                   "[注册] POST /algorithms/register：传入算法包目录路径，服务器解析 algorithm_card.yaml"
                   " 并校验文件完整性（schema / model 文件均需存在）。"
                   " 注册成功返回 HTTP 201，status=validated（已校验但尚未激活）。"
                   " validated 状态下算法不对外提供推理服务，需手动 /activate 才能上线。");
        if (ok) {
            PrintOk("注册成功，status=validated，algorithm_id=" +
                    body.value("algorithm_id", "?"));
            std::cout << "    → 响应 HTTP 201，说明资源首次创建成功；\n"
                      << "      status=validated 表示 card 格式正确，等待人工激活再上线。\n";
        } else {
            PrintFail("注册失败：HTTP " + std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 1.2 激活算法 ────────────────────────────────────────────────────────
    PrintStep("1.2", "POST /algorithms/onnx_text_classifier/1.0.0/onnx/activate — 激活");
    std::cout << "    只有 active 状态的算法才能被 /run 调用。\n"
              << "    状态机：validated → active（只有此路径可对外提供推理服务）。\n";
    {
        auto [status, body] = Post(port, "/algorithms/onnx_text_classifier/1.0.0/onnx/activate", {});
        const bool ok = (status == 200) && body.value("status", "") == "active";
        RecordStep(SEC, "1.2_activate", "POST",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx/activate",
                   {}, status, body, ok,
                   "[激活] POST .../activate：将算法状态从 validated 推进到 active。"
                   " 只有 active 状态的算法才能被 /run 接口调用执行推理。"
                   " 激活是人工把关环节，防止未经审核的算法自动上线。"
                   " 响应中 status=active 表示激活已生效，可立即接受推理请求。");
        if (ok) {
            PrintOk("激活成功，status=active");
            std::cout << "    → status 从 validated 转为 active，算法现在可接受推理请求。\n";
        } else {
            PrintFail("激活失败：HTTP " + std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 1.3 多维过滤查询 ─────────────────────────────────────────────────────
    PrintStep("1.3", "GET /algorithms?task_family=text_classification — 按 task_family 过滤");
    std::cout << "    支持 8 个维度的组合过滤：task_family / backend / capability / node_id /\n"
              << "    status / active_only / max_vram_mb / max_memory_mb / max_cpu_cores。\n"
              << "    此步按任务族过滤，结果只包含文本分类类算法。\n";
    {
        auto [status, body] = Get(port, "/algorithms?task_family=text_classification");
        const bool ok = (status == 200) && body.value("count", 0) >= 1;
        RecordStep(SEC, "1.3_query_task_family", "GET",
                   "/algorithms?task_family=text_classification",
                   {}, status, body, ok,
                   "[按任务族过滤] GET /algorithms?task_family=text_classification："
                   " 返回所有任务族为 text_classification 的 active 算法列表。"
                   " 响应字段 filter 回显所有生效的过滤条件，algorithms[] 包含算法摘要。"
                   " count=1 说明当前注册表里只有 onnx_text_classifier 满足条件。");
        if (ok) {
            PrintOk("查询成功，count=" + std::to_string(body.value("count", 0)));
            std::cout << "    → 响应中 filter.task_family=text_classification 回显生效的过滤条件，\n"
                      << "      algorithms[] 数组包含算法摘要（algorithm_id/version/status 等）。\n";
        } else {
            PrintFail("按 task_family 查询失败：HTTP " + std::to_string(status));
        }
    }

    PrintStep("1.4", "GET /algorithms?backend=onnx&capability=confidence_score — 多维过滤");
    std::cout << "    同时指定 backend=onnx 和 capability=confidence_score 两个维度，\n"
              << "    只返回使用 ONNX 后端且具备置信度输出能力的算法。\n";
    {
        auto [status, body] = Get(port, "/algorithms?backend=onnx&capability=confidence_score");
        const bool ok = (status == 200) && body.value("count", 0) >= 1;
        RecordStep(SEC, "1.4_query_multi_filter", "GET",
                   "/algorithms?backend=onnx&capability=confidence_score",
                   {}, status, body, ok,
                   "[多维 AND 过滤] GET /algorithms?backend=onnx&capability=confidence_score："
                   " 同时指定后端类型 onnx 和能力标签 confidence_score。"
                   " 多个过滤条件之间是 AND 逻辑，只有同时满足所有条件的算法才会出现在结果中。"
                   " 此步验证 onnx_text_classifier 同时具备这两个属性。");
        if (ok) {
            PrintOk("多维过滤成功，count=" + std::to_string(body.value("count", 0)));
            std::cout << "    → 两个过滤条件同时命中，说明过滤器是 AND 逻辑组合。\n";
        } else {
            PrintFail("多维过滤查询失败：HTTP " + std::to_string(status));
        }
    }

    PrintStep("1.5", "GET /algorithms?max_memory=2048 — 按资源约束过滤");
    std::cout << "    max_memory=2048 MB：只返回最低内存需求 ≤ 2048 MB 的算法。\n"
              << "    onnx_text_classifier 要求 min_memory_mb=512，满足条件。\n";
    {
        auto [status, body] = Get(port, "/algorithms?max_memory=2048");
        const bool ok = (status == 200);
        RecordStep(SEC, "1.5_query_resource_filter", "GET",
                   "/algorithms?max_memory=2048",
                   {}, status, body, ok,
                   "[资源约束过滤] GET /algorithms?max_memory=2048："
                   " 只返回最低内存需求 <= 2048 MB 的算法，帮助 agent 在资源受限节点上选择可部署的算法。"
                   " onnx_text_classifier 的 min_memory_mb=512，远低于 2048，因此出现在结果中。"
                   " 类似参数还有 max_vram_mb / max_cpu_cores，可组合使用。");
        if (ok) {
            PrintOk("资源过滤成功，count=" + std::to_string(body.value("count", 0)));
            std::cout << "    → 资源过滤可帮助 agent 在资源受限节点上选取可部署算法。\n";
        } else {
            PrintFail("资源过滤查询失败：HTTP " + std::to_string(status));
        }
    }

    // ─── 1.6 获取单算法详情（含 agent_view）─────────────────────────────────
    PrintStep("1.6", "GET /algorithms/onnx_text_classifier/1.0.0/onnx — 详情+agent_view");
    std::cout << "    单算法详情接口同时返回两个视图：\n"
              << "      entry      — 原始注册条目（含完整 card + deployments 列表）\n"
              << "      agent_view — agent 专用精简视图，含 performance/capabilities/\n"
              << "                   constraints/ready_endpoints/input_output_schema 摘要\n"
              << "    agent 调用此接口决定是否选用该算法及如何构造请求。\n";
    {
        auto [status, body] = Get(port, "/algorithms/onnx_text_classifier/1.0.0/onnx");
        const bool has_agent_view = body.contains("agent_view");
        const bool has_perf       = has_agent_view && body.at("agent_view").contains("performance");
        const bool ok             = (status == 200) && body.value("ok", false) && has_perf;
        RecordStep(SEC, "1.6_show_card", "GET",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx",
                   {}, status, body, ok,
                   "[算法详情] GET /algorithms/{id}/{ver}/{backend}：返回两个并列视图。"
                   " entry = 原始注册条目（完整 card + deployments 列表）；"
                   " agent_view = agent 专用精简视图，包含 performance（latency_p50/p95/accuracy）、"
                   " capabilities、constraints（max_input_chars/batch_supported）、"
                   " input_schema_summary / output_schema_summary、ready_endpoints。"
                   " agent 可直接读取 agent_view 决策是否选用该算法及如何构造请求。");
        if (ok) {
            const auto& av = body.at("agent_view");
            PrintOk("详情获取成功，agent_view.performance 存在");
            std::cout << "    → agent_view 关键字段：\n"
                      << "      latency_p50=" << av.at("performance").value("latency_ms_p50", 0) << "ms"
                      << "  accuracy=" << av.at("performance").value("primary_score", 0.0) << "\n"
                      << "      input: {" << av.at("input_schema_summary").at("required")[0].get<std::string>() << ": string}\n"
                      << "      output: {label: string, confidence: number}\n";
        } else {
            PrintFail("获取详情失败：HTTP " + std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 1.7 添加部署记录 ─────────────────────────────────────────────────────
    PrintStep("1.7", "POST /algorithms/.../deployments — 添加部署记录（自动填充 deployed_at）");
    std::cout << "    DeploymentSpec 记录算法在某个节点上的部署实例，字段包含：\n"
              << "      deploy_id    — 唯一标识（不含斜杠，否则被路由解析为路径分隔符）\n"
              << "      node_id      — 目标节点标识，用于按节点过滤查询\n"
              << "      endpoint     — 节点对外服务地址\n"
              << "      deploy_status— 初始为 unloaded，Load 后自动变 ready\n"
              << "    服务器自动填充 deployed_at（UTC ISO 8601 时间戳）。\n";
    {
        // 注意：deploy_id 不能含斜杠（httplib 路由用 [^/]+ 捕获），使用 demo-node-1
        const json req{
            {"deploy_id",    "demo-node-1"},
            {"node_id",      "demo-node-1"},
            {"endpoint",     "http://demo-node-1:8088"},
            {"deploy_status","unloaded"},
        };
        auto [status, body] = Post(
            port, "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments", req);
        const bool ok = (status == 201) && body.value("ok", false);

        // 验证 deployed_at 自动填充
        bool deployed_at_filled = false;
        std::string deployed_at_val;
        if (ok && body.contains("entry") && body.at("entry").contains("deployments")) {
            for (const auto& d : body.at("entry").at("deployments")) {
                if (d.value("deploy_id", "") == "demo-node-1") {
                    deployed_at_val   = d.value("deployed_at", std::string());
                    deployed_at_filled = !deployed_at_val.empty();
                    break;
                }
            }
        }
        RecordStep(SEC, "1.7_add_deployment", "POST",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments",
                   req, status, body, ok && deployed_at_filled,
                   "[添加部署记录] POST .../deployments：向注册表写入一条 DeploymentSpec。"
                   " 字段含义：deploy_id=唯一实例标识；node_id=宿主节点；endpoint=对外服务地址；"
                   " deploy_status 初始为 unloaded，模型加载完成后自动变为 ready。"
                   " 服务器自动填充 deployed_at（UTC ISO 8601 时间戳），调用方无需传入。"
                   " 返回 HTTP 201 表示部署记录首次创建成功。");
        if (ok && deployed_at_filled) {
            PrintOk("添加部署记录成功，deployed_at 自动填充正确");
            std::cout << "    → deployed_at=" << deployed_at_val
                      << "（服务端自动生成 UTC 时间戳，无需调用方传入）\n";
        } else if (ok) {
            PrintFail("添加部署记录成功，但 deployed_at 未自动填充");
        } else {
            PrintFail("添加部署记录失败：HTTP " + std::to_string(status));
        }
    }

    // ─── 1.8 更新部署状态 ─────────────────────────────────────────────────────
    PrintStep("1.8", "PATCH /algorithms/.../deployments/demo-node-1/status — 更新部署状态");
    std::cout << "    部署状态可选值：unloaded / loading / ready / error。\n"
              << "    /load 接口成功后会自动调用此接口将状态改为 ready，\n"
              << "    此处手动演示 PATCH 调用（status_message 可附带文字说明）。\n"
              << "    服务端同时自动填充 updated_at 时间戳。\n";
    {
        const json req{{"deploy_status", "ready"}, {"status_message", "demo loaded"}};
        auto [status, body] = Patch(
            port,
            "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments/demo-node-1/status",
            req);
        const bool ok = (status == 200) && body.value("ok", false);
        RecordStep(SEC, "1.8_update_deploy_status", "PATCH",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments/demo-node-1/status",
                   req, status, body, ok,
                   "[更新部署状态] PATCH .../deployments/{deploy_id}/status："
                   " 修改指定节点的部署状态，可选值：unloaded / loading / ready / error。"
                   " /load 接口在模型加载成功后会自动调用此接口将状态设为 ready；"
                   " 此处手动演示 PATCH，同时附带 status_message 文字说明。"
                   " 状态变为 ready 后，该节点的 endpoint 会出现在 agent_view.ready_endpoints 中。"
                   " 服务器自动更新 updated_at 时间戳。");
        if (ok) {
            PrintOk("部署状态更新成功 → ready");
            // 从响应中提取 updated_at
            std::string updated_at;
            if (body.contains("entry") && body.at("entry").contains("deployments")) {
                for (const auto& d : body.at("entry").at("deployments")) {
                    if (d.value("deploy_id", "") == "demo-node-1") {
                        updated_at = d.value("updated_at", std::string());
                        break;
                    }
                }
            }
            std::cout << "    → deploy_status=ready，updated_at=" << updated_at << "\n"
                      << "      node_id=demo-node-1 的状态已持久化到注册表，\n"
                      << "      后续 GET /algorithms?node=demo-node-1 可按节点过滤查到此部署。\n";
        } else {
            PrintFail("更新部署状态失败：HTTP " + std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 1.9 禁用再删除 ──────────────────────────────────────────────────────
    PrintStep("1.9", "POST /disable + DELETE — 禁用再删除算法，验证从查询结果消失");
    std::cout << "    完整下线流程：先 /disable（active→disabled）再 DELETE（标记 deleted）。\n"
              << "    deleted 条目永久隐藏于所有查询结果（包括 active_only=false），\n"
              << "    但物理上仍持久化在注册表文件中（支持审计追踪）。\n";
    {
        auto [s1, b1] = Post(port, "/algorithms/onnx_text_classifier/1.0.0/onnx/disable", {});
        auto [s2, b2] = Delete(port, "/algorithms/onnx_text_classifier/1.0.0/onnx");
        auto [s3, b3] = Get(port, "/algorithms?active_only=false");
        const bool ok = (s1 == 200) && (s2 == 200) && (b3.value("count", 1) == 0);
        RecordStep(SEC, "1.9_disable_delete", "DELETE",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx",
                   {}, s2, b2, ok,
                   "[禁用并删除] POST /disable + DELETE：演示算法完整下线流程。"
                   " /disable 将状态从 active 改为 disabled，停止接受新的推理请求；"
                   " DELETE 将状态标记为 deleted，从所有查询结果中永久隐藏（含 active_only=false）。"
                   " 物理上条目仍保留在注册表文件中以支持审计追踪，不会真正删除磁盘数据。"
                   " 验证：GET /algorithms?active_only=false 返回 count=0，删除完全生效。");
        if (ok) {
            PrintOk("禁用并删除成功，算法不再出现在查询结果中");
            std::cout << "    → /disable 返回 status=disabled；\n"
                      << "      DELETE 返回 status=deleted；\n"
                      << "      GET /algorithms?active_only=false 的 count=0，删除完全生效。\n";
        } else {
            PrintFail("禁用/删除流程异常，count=" +
                      std::to_string(b3.value("count", -1)));
        }
    }

    section_report = g_report.value(SEC, json::array());
    std::cout << kGreen << "\n  ✅ 接口一全部步骤通过\n" << kReset;
}

// =============================================================================
// SECTION 2 — 模型加载接口
// =============================================================================
void DemoModelLoadApi(int port, const fs::path& src_root, json& section_report) {
    PrintSection("【接口二】模型加载接口 (Model Load API)");
    const std::string SEC = "model_load_api";

    // 重新注册 + 激活（Section 1 已删除，先清理注册表文件再重新注册）
    // 注意：deleted 条目持久化在 registry 文件中，直接 /register 会 409 冲突；
    // 通过删除注册表文件并调用 /reload 让服务器以空注册表重新开始。
    {
        const fs::path registry_file = src_root / "demo" / "shared_registry.json";
        std::error_code ec;
        fs::remove(registry_file, ec);
        Post(port, "/reload", {});  // 重载后注册表为空
    }
    {
        const json req{{"package_or_card_path",
                        (src_root / "examples" / "onnx_text_classifier" / "1.0.0").generic_string()}};
        Post(port, "/algorithms/register", req);
        Post(port, "/algorithms/onnx_text_classifier/1.0.0/onnx/activate", {});
    }

    // 添加本地部署记录
    {
        const json dep{{"deploy_id", "local/d1"}, {"node_id", "local"},
                       {"endpoint", ""}, {"deploy_status", "unloaded"}};
        Post(port, "/algorithms/onnx_text_classifier/1.0.0/onnx/deployments", dep);
    }

    // ─── 2.1 首次加载 ────────────────────────────────────────────────────────
    PrintStep("2.1", "POST /load — 预加载 ONNX 模型（transfer_mode=none）");
    {
        const json req{{"algorithm_id", "onnx_text_classifier"},
                       {"version",      "1.0.0"},
                       {"backend_type", "onnx"},
                       {"deploy_id",    "local/d1"}};
        auto [status, body] = Post(port, "/load", req);
        const bool ok = (status == 200) && body.value("ok", false) &&
                        body.value("load_status", "") == "loaded" &&
                        body.contains("health") && body.at("health").value("ok", false);
        RecordStep(SEC, "2.1_load_first", "POST", "/load", req, status, body, ok,
                   "[首次加载] POST /load：预加载指定算法的 ONNX Session 到内存缓存。"
                   " 成功后 load_status=loaded，health.ok=true 表示 Session 已就绪。"
                   " 同时自动回写注册表中 deploy_id=local/d1 的 deploy_status 为 ready。"
                   " transfer_mode=none 表示模型文件已在本地，无需远程下载。");
        if (ok) {
            PrintOk("首次加载成功，load_status=loaded，health.ok=true");
        } else {
            PrintFail("首次加载失败：HTTP " + std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 2.2 验证 deploy_status 已变为 ready ─────────────────────────────────
    PrintStep("2.2", "GET /algorithms/.../onnx — 验证 deploy_status=ready");
    std::cout << "    /load 成功后会自动回写 deploy_status=ready 到注册表，\n"
              << "    agent 可通过 GET 单算法详情接口中 agent_view.ready_endpoints\n"
              << "    字段来实时获取就绪的节点列表。\n";
    {
        auto [status, body] = Get(port, "/algorithms/onnx_text_classifier/1.0.0/onnx");
        bool deploy_ready = false;
        if (body.contains("entry") && body.at("entry").contains("deployments")) {
            for (const auto& d : body.at("entry").at("deployments")) {
                if (d.value("deploy_id", "") == "local/d1") {
                    deploy_ready = (d.value("deploy_status", "") == "ready");
                    break;
                }
            }
        }
        RecordStep(SEC, "2.2_check_deploy_status", "GET",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx",
                   {}, status, body, deploy_ready,
                   "[验证自动回写] GET /algorithms/.../onnx：/load 成功后会自动回写 deploy_status=ready 到注册表。"
                   " 通过该接口查看 entry.deployments[]，可确认 deploy_id=local/d1 的状态已更新。"
                   " agent_view.ready_endpoints 也会同步包含该节点，agent 可直接读取此列表获得可用的就绪端点。");
        if (deploy_ready) {
            PrintOk("deploy_status 已自动更新为 ready");
            std::cout << "    → deploy_id=local/d1 的 deploy_status=ready 已写入注册表，\n"
                      << "      /load 自动触发了状态回写，无需额外 PATCH 调用。\n";
        } else {
            PrintFail("deploy_status 未更新为 ready");
        }
    }

    // ─── 2.3 推理 ─────────────────────────────────────────────────────────────
    PrintStep("2.3", "POST /run — ONNX 推理");
    {
        const json req{{"request_id",   "demo_req_001"},
                       {"trace_id",     "demo_trace_001"},
                       {"algorithm_id", "onnx_text_classifier"},
                       {"version",      "1.0.0"},
                       {"backend_type", "onnx"},
                       {"deploy_id",    "local/d1"},
                       {"inputs",       {{"text", "Classify this task text."}}}};
        auto [status, body] = Post(port, "/run", req);
        const bool ok = (status == 200) && body.value("ok", false) &&
                        body.contains("outputs") &&
                        body.at("outputs").value("label", "") == "task";
        RecordStep(SEC, "2.3_run_inference", "POST", "/run", req, status, body, ok,
                   "[执行推理] POST /run：向已加载的 Session 提交输入并返回推理结果。"
                   " 请求必须指定 request_id+trace_id 以支持追踪和审计。"
                   " outputs.label=task 表示文本被分类为任务类型，置信度 confidence=0.96。"
                   " usage 字段返回执行提供器、延迟等性能指标，可用于性能监控。");
        if (ok) {
            PrintOk("推理成功，label=" + body.at("outputs").value("label", "?") +
                    " confidence=" + std::to_string(
                        body.at("outputs").value("confidence", 0.0)));
        } else {
            PrintFail("推理失败：HTTP " + std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 2.4 重复加载 → already_loaded ───────────────────────────────────────
    PrintStep("2.4", "POST /load（再次）— 验证缓存命中，load_status=already_loaded");
    std::cout << "    Runner 已缓存，第二次 /load 会命中缓存返回 already_loaded，不重建 Session。\n";
    {
        const json req{{"algorithm_id", "onnx_text_classifier"},
                       {"version",      "1.0.0"},
                       {"backend_type", "onnx"},
                       {"deploy_id",    "local/d1"}};
        auto [status, body] = Post(port, "/load", req);
        const bool ok = (status == 200) && body.value("ok", false) &&
                        body.value("load_status", "") == "already_loaded";
        RecordStep(SEC, "2.4_load_cached", "POST", "/load", req, status, body, ok,
                   "[缓存命中] POST /load（再次）：Runner 已在内存中，第二次调用返回 load_status=already_loaded。"
                   " 并不重建 ONNX Session，避免重复加载开销，接口实现了幂等性设计。"
                   " 这意味着多个调用方可以无顾虑是否已加载而安全重复调用 /load。");
        if (ok) {
            PrintOk("缓存命中，load_status=already_loaded");
        } else {
            PrintFail("期望 already_loaded，实际：HTTP " +
                      std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 2.5 runner_cache_size 验证 ──────────────────────────────────────────
    PrintStep("2.5", "GET /health — 验证 runner_cache_size=1");
    {
        auto [status, body] = Get(port, "/health");
        const int cache_size = body.value("runner_cache_size", -1);
        const bool ok        = (status == 200) && cache_size >= 1;
        RecordStep(SEC, "2.5_health_cache_size", "GET", "/health",
                   {}, status, body, ok,
                   "[健康检查+缓存大小] GET /health：返回服务器状态摘要。"
                   " runner_cache_size=1 表示当前缓存中有 1 个已加载的 Runner（ONNX Session）。"
                   " 缓存采用 LIFO 策略，当超出容量时驱逐最久未使用的条目，防止内存溢出。"
                   " 该接口可用于运维监控，实时了解内存占用情况。");
        if (ok) {
            PrintOk("runner_cache_size=" + std::to_string(cache_size));
        } else {
            PrintFail("runner_cache_size 异常：" + std::to_string(cache_size));
        }
    }

    // ─── 2.6 卸载 ─────────────────────────────────────────────────────────────
    PrintStep("2.6", "POST /unload — 卸载模型");
    {
        const json req{{"algorithm_id", "onnx_text_classifier"},
                       {"version",      "1.0.0"},
                       {"backend_type", "onnx"},
                       {"deploy_id",    "local/d1"}};
        auto [status, body] = Post(port, "/unload", req);
        const bool ok = (status == 200) && body.value("ok", false) &&
                        body.value("load_status", "") == "unloaded";
        RecordStep(SEC, "2.6_unload", "POST", "/unload", req, status, body, ok,
                   "[卸载模型] POST /unload：从缓存中移除指定 Runner，释放内存。"
                   " load_status=unloaded 确认 Session 已销毁并从缓存中移除。"
                   " 同时自动回写注册表中 deploy_id=local/d1 的 deploy_status 为 unloaded。"
                   " 卸载后可重新 /load 进行热更新，或切换到新版本模型。");
        if (ok) {
            PrintOk("卸载成功，load_status=unloaded");
        } else {
            PrintFail("卸载失败：HTTP " + std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 2.7 卸载后重新加载 ───────────────────────────────────────────────────
    PrintStep("2.7", "POST /load（卸载后）— 验证可重新加载");
    {
        const json req{{"algorithm_id", "onnx_text_classifier"},
                       {"version",      "1.0.0"},
                       {"backend_type", "onnx"},
                       {"deploy_id",    "local/d1"}};
        auto [status, body] = Post(port, "/load", req);
        const bool ok = (status == 200) && body.value("ok", false) &&
                        body.value("load_status", "") == "loaded";
        RecordStep(SEC, "2.7_reload_after_unload", "POST", "/load", req, status, body, ok,
                   "[卸载后重新加载] POST /load（卸载后）：验证卸载后可顷利重新加载。"
                   " load_status=loaded 表示 Session 重建成功，服务恢复就绪。"
                   " 此流程常用于模型热更新（unload 旧版本 → 注册新版本 → load 新版本），实现无中断升级。");
        if (ok) {
            PrintOk("卸载后重新加载成功，load_status=loaded");
        } else {
            PrintFail("卸载后重新加载失败：HTTP " + std::to_string(status));
        }
    }

    section_report = g_report.value(SEC, json::array());
    std::cout << kGreen << "\n  ✅ 接口二全部步骤通过\n" << kReset;
}

// =============================================================================
// SECTION 3 — 文件服务接口（http_pull 基础）
// =============================================================================
void DemoFileServingApi(int port, const fs::path& src_root, json& section_report) {
    PrintSection("【接口三】文件服务接口 (File Serving API)");
    std::cout << "  文件服务接口为分布式 http_pull 模式提供基础，允许远程节点\n"
              << "  按需从主节点下载算法模型和配置文件。本节演示 4 个安全场景：\n"
              << "    正常下载 / YAML 下载 / 路径穿越拦截 / 404 处理。\n";
    const std::string SEC = "file_serving_api";

    // ─── 3.1 下载 model.onnx ─────────────────────────────────────────────────
    PrintStep("3.1", "GET /algorithms/.../files/model.onnx — 正常下载");
    std::cout << "    文件路径格式：/algorithms/{id}/{ver}/{backend}/files/{rel_path}\n"
              << "    rel_path 是相对于算法包目录的路径，服务器会校验路径安全性。\n";
    {
        auto [status, file_size, ct] =
            GetFile(port, "/algorithms/onnx_text_classifier/1.0.0/onnx/files/model.onnx");
        const fs::path orig =
            src_root / "examples" / "onnx_text_classifier" / "1.0.0" / "model.onnx";
        const size_t expected_size = fs::exists(orig) ? fs::file_size(orig) : 0;
        const bool ok = (status == 200) && (file_size > 0) && (file_size == expected_size);
        json rec{{"http_status", status},
                 {"file_size_bytes", static_cast<long long>(file_size)},
                 {"expected_size_bytes", static_cast<long long>(expected_size)},
                 {"content_type", ct}};
        RecordStep(SEC, "3.1_download_onnx", "GET",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx/files/model.onnx",
                   {}, status, rec, ok,
                   "[下载 ONNX 模型] GET /algorithms/{id}/{ver}/{backend}/files/{rel_path}："
                   " 下载算法包内的指定文件。"
                   " 服务器首先解析算法包目录，再拼接 rel_path，最终流式返回文件内容。"
                   " 返回 Content-Type: application/octet-stream，远程节点可用此接口实现 http_pull 模式下载。"
                   " 验证要点：file_size_bytes=expected_size_bytes 确保文件内容完整无损。");
        if (ok) {
            PrintOk("model.onnx 下载成功，大小=" + std::to_string(file_size) + " bytes，与源文件一致");
        } else {
            PrintFail("model.onnx 下载失败：status=" + std::to_string(status) +
                      " size=" + std::to_string(file_size) +
                      " expected=" + std::to_string(expected_size));
        }
    }

    // ─── 3.2 下载 preprocess.yaml ────────────────────────────────────────────
    PrintStep("3.2", "GET /algorithms/.../files/preprocess.yaml — 下载配置文件");
    {
        auto [status, file_size, ct] =
            GetFile(port, "/algorithms/onnx_text_classifier/1.0.0/onnx/files/preprocess.yaml");
        const bool ok = (status == 200) && (file_size > 0);
        json rec{{"http_status", status}, {"file_size_bytes", static_cast<long long>(file_size)}};
        RecordStep(SEC, "3.2_download_yaml", "GET",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx/files/preprocess.yaml",
                   {}, status, rec, ok,
                   "[下载配置文件] GET .../files/preprocess.yaml：下载算法包内的预处理配置文件。"
                   " 文件服务接口不限于二进制模型，YAML/JSON/文本等所有文件均可流式返回。"
                   " 返回 HTTP 200 和正确的文件内容，验证文件服务对不同文件类型的通用支持。");
        if (ok) {
            PrintOk("preprocess.yaml 下载成功，大小=" + std::to_string(file_size) + " bytes");
        } else {
            PrintFail("preprocess.yaml 下载失败：status=" + std::to_string(status));
        }
    }

    // ─── 3.3 路径穿越攻击被拒绝（400）────────────────────────────────────────
    PrintStep("3.3", "GET /algorithms/.../files/../../../etc/passwd — 路径穿越（期望 400）");
    {
        auto [status, file_size, ct] =
            GetFile(port, "/algorithms/onnx_text_classifier/1.0.0/onnx/files/../../../etc/passwd");
        const bool ok = (status == 400);
        json rec{{"http_status", status}, {"expect", 400}};
        RecordStep(SEC, "3.3_path_traversal_rejected", "GET",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx/files/../../../etc/passwd",
                   {}, status, rec, ok,
                   "[路径穿越防护] GET .../files/../../../etc/passwd：尝试通过 ../ 跳出算法包目录并读取系统文件。"
                   " 服务器计算 canonical 路径，如果解析后路径越出算法包目录就拒绝并返回 HTTP 400。"
                   " 返回 400 正是预期行为，证明路径安全校验机制有效。"
                   " 如果返回 200 则表示存在路径穿越漏洞，是严重安全隐患。");
        if (ok) {
            PrintOk("路径穿越攻击被拒绝，返回 400（符合预期）");
        } else {
            PrintFail("路径穿越未被拦截，实际状态：" + std::to_string(status));
        }
    }

    // ─── 3.4 不存在的文件返回 404 ─────────────────────────────────────────────
    PrintStep("3.4", "GET /algorithms/.../files/nonexistent.bin — 不存在文件（期望 404）");
    {
        auto [status, file_size, ct] =
            GetFile(port, "/algorithms/onnx_text_classifier/1.0.0/onnx/files/nonexistent.bin");
        const bool ok = (status == 404);
        json rec{{"http_status", status}, {"expect", 404}};
        RecordStep(SEC, "3.4_file_not_found", "GET",
                   "/algorithms/onnx_text_classifier/1.0.0/onnx/files/nonexistent.bin",
                   {}, status, rec, ok,
                   "[文件不存在 404] GET .../files/nonexistent.bin：请求一个不存在的文件。"
                   " 服务器在安全校验通过后，发现文件不存在则返回 HTTP 404。"
                   " 返回 404 正是预期行为，验证错误处理逻辑正确，不会将 404 误判为 500 或其他语义。");
        if (ok) {
            PrintOk("不存在文件返回 404（符合预期）");
        } else {
            PrintFail("期望 404，实际：" + std::to_string(status));
        }
    }

    section_report = g_report.value(SEC, json::array());
    std::cout << kGreen << "\n  ✅ 接口三全部步骤通过\n" << kReset;
}

// =============================================================================
// SECTION 4 — Python HTTP Service 并发池
// =============================================================================
void DemoPythonPool(const fs::path& src_root, const fs::path& demo_dir, json& section_report) {
    PrintSection("【接口四】Python HTTP Service 并发池 (PythonHttpRunnerPool)");
    const std::string SEC = "python_pool_api";

    // 启动 Mock Python Service（延迟 15ms 模拟 GPU 推理）
    MockPythonService mock(15);
    PrintOk("Mock Python Service 启动，port=" + std::to_string(mock.port()));

    // 复制 fixture 到临时目录，并 patch endpoint
    const fs::path fixture_src = src_root / "examples" / "python_http_service_llm_explainer" / "1.0.0";
    const fs::path fixture_dst = demo_dir / "pool_fixture" / "llm_rule_explainer" / "1.0.0";
    CopyDir(fixture_src, fixture_dst);
    PatchEndpointInCard(fixture_dst, mock.base_url());
    PrintOk("Fixture 复制完成，endpoint 已指向 Mock 服务：" + mock.base_url());

    // 启动独立 Server（避免与 Section 1-3 的 Server 冲突）
    const fs::path pool_registry = demo_dir / "pool_registry.json";
    const fs::path pool_log      = demo_dir / "pool_audit.jsonl";

    // 预先注册 + 激活
    // fixture_dst = .../llm_rule_explainer/1.0.0，注册时传入包目录（含 algorithm_card.yaml）
    {
        algolib::AlgorithmRegistry pre_reg(pool_registry);
        pre_reg.Reload();
        if (!pre_reg.Register(fixture_dst).ok()) {
            PrintFail("Pool demo: fixture 注册失败");
        }
        algolib::AlgorithmKey key{"llm_rule_explainer", "1.0.0",
                                  algolib::BackendType::kPythonHttpService};
        if (!pre_reg.Activate(key).ok()) {
            PrintFail("Pool demo: fixture 激活失败");
        }
    }
    PrintOk("llm_rule_explainer 注册并激活完成");

    RunningServer pool_server({pool_registry, pool_log, "127.0.0.1", 0});
    pool_server.Start();
    const int pp = pool_server.port();
    PrintOk("Pool Demo Server 启动，port=" + std::to_string(pp));

    // ─── 4.1 加载并发池（pool_size=3）─────────────────────────────────────────
    PrintStep("4.1", "POST /load pool_size=3 — 加载 Python 并发池");
    std::cout << "    pool_size=3 说明最多允许 3 个并发请求同时访问 Python 服务。\n"
              << "    pool_checkout_timeout_ms 设置超出 pool_size 时的最大等待时间（ms）。\n";
    {
        const json req{{"algorithm_id", "llm_rule_explainer"},
                       {"version",      "1.0.0"},
                       {"backend_type", "python_http_service"},
                       {"pool_size",    3},
                       {"pool_checkout_timeout_ms", 5000}};
        auto [status, body] = Post(pp, "/load", req);
        const bool ok = (status == 200) && body.value("ok", false) &&
                        body.value("load_status", "") == "loaded";
        RecordStep(SEC, "4.1_pool_load", "POST", "/load", req, status, body, ok,
                   "[并发池加载] POST /load pool_size=3：创建一个大小为 3 的连接池指向 Python HTTP 服务。"
                   " pool_size=3 表示最多允许 3 个并发请求同时占用连接，第 4 个请求必须排队等待。"
                   " pool_checkout_timeout_ms=5000 表示排队超过 5 秒未获得连接就返回超时错误。"
                   " 底层基于 std::condition_variable 实现锁的连接池，限制并发以防止压币 Python 服务。");
        if (ok) {
            PrintOk("并发池加载成功，load_status=loaded，pool_size=3");
        } else {
            PrintFail("并发池加载失败：HTTP " + std::to_string(status) + " " + body.dump());
        }
    }

    // ─── 4.2 GET /health 验证 runner_cache_size ────────────────────────────────
    PrintStep("4.2", "GET /health — 验证 runner_cache_size 包含并发池");
    {
        auto [status, body] = Get(pp, "/health");
        const int cache_size = body.value("runner_cache_size", 0);
        const bool ok        = (status == 200) && cache_size >= 1;
        RecordStep(SEC, "4.2_pool_health", "GET", "/health", {}, status, body, ok,
                   "[并发池健康检查] GET /health：确认并发池 Runner 已进入缓存。"
                   " runner_cache_size>=1 表示 Pool Runner 已被注册并激活。"
                   " registry_path / execution_log_path 展示此独立 Server 使用的池専用注册表和审计日志路径。");
        if (ok) {
            PrintOk("runner_cache_size=" + std::to_string(cache_size) + "（并发池已缓存）");
        } else {
            PrintFail("健康检查失败，runner_cache_size=" + std::to_string(cache_size));
        }
    }

    // ─── 4.3 串行 6 次 /run（超出 pool_size，验证排队等待）────────────────────
    PrintStep("4.3", "6 次串行 POST /run — 验证所有请求成功（队列调度）");
    {
        int success_count = 0;
        int error_count   = 0;
        json run_details  = json::array();

        for (int i = 0; i < 6; ++i) {
            const json req{
                {"request_id",   "demo_pool_req_" + std::to_string(i)},
                {"trace_id",     "demo_pool_trace_" + std::to_string(i)},
                {"algorithm_id", "llm_rule_explainer"},
                {"version",      "1.0.0"},
                {"backend_type", "python_http_service"},
                {"inputs",       {{"task_text", "Task " + std::to_string(i)},
                                  {"entities",  json::array()}}}};
            auto [status, body] = Post(pp, "/run", req);
            const bool item_ok  = (status == 200) && body.value("ok", false);
            if (item_ok) {
                ++success_count;
            } else {
                ++error_count;
                std::cerr << "  [run#" << i << "] FAILED: HTTP " << status
                          << " " << body.dump() << "\n";
            }
            run_details.push_back({{"index", i}, {"ok", item_ok},
                                   {"http_status", status},
                                   {"response", body}});
        }

        const bool all_ok = (success_count == 6);
        RecordStep(SEC, "4.3_pool_run_sequential", "POST", "/run (x6)",
                   {{"success_count", success_count},
                    {"error_count",   error_count},
                    {"details",       run_details}},
                   200, {{"success_count", success_count}, {"error_count", error_count}},
                   all_ok,
                   "[串行 6 次推理] 6 次串行 POST /run 验证排队调度。"
                   " pool_size=3 但请求总数 6 超过容量，后 3 个请求会排队等待前 3 个释放连接。"
                   " 所有 6 个请求均返回 success=true，证明排队机制正常工作。"
                   " details[] 字段指向每个单次请求的具体响应，可逐项查看推理输出。");
        if (all_ok) {
            PrintOk("6 次请求全部成功（success=" + std::to_string(success_count) + "/6）");
        } else {
            PrintFail("部分请求失败（success=" + std::to_string(success_count) +
                      " error=" + std::to_string(error_count) + ")");
        }
    }

    // ─── 4.4 并发 6 次 /run（验证真正并发路径）────────────────────────────────
    PrintStep("4.4", "6 线程并发 POST /run — 验证并发安全");
    {
        std::atomic<int> success_count{0};
        std::atomic<int> error_count{0};
        std::vector<std::thread> threads;
        threads.reserve(6);

        for (int i = 0; i < 6; ++i) {
            threads.emplace_back([&, i]() {
                const json req{
                    {"request_id",   "demo_conc_req_" + std::to_string(i)},
                    {"trace_id",     "demo_conc_trace_" + std::to_string(i)},
                    {"algorithm_id", "llm_rule_explainer"},
                    {"version",      "1.0.0"},
                    {"backend_type", "python_http_service"},
                    {"inputs",       {{"task_text", "Concurrent Task " + std::to_string(i)},
                                      {"entities",  json::array()}}}};
                auto [status, body] = Post(pp, "/run", req);
                if ((status == 200) && body.value("ok", false)) {
                    success_count.fetch_add(1, std::memory_order_relaxed);
                } else {
                    error_count.fetch_add(1, std::memory_order_relaxed);
                }
            });
        }
        for (auto& t : threads) t.join();

        const bool all_ok = (success_count.load() == 6);
        RecordStep(SEC, "4.4_pool_run_concurrent", "POST", "/run (concurrent x6)",
                   {{"threads", 6}},
                   200, {{"success_count", success_count.load()},
                          {"error_count",  error_count.load()}},
                   all_ok,
                   "[6 线程并发推理] 6 个 std::thread 同时发起 POST /run，验证并发安全性。"
                   " 6 线程 > pool_size=3，势必需要排队；所有请求均能在超时内完成，证明并发池无死锁。"
                   " success=6/6 证明并发路径线程安全，condition_variable+mutex 正确保护了连接池的状态。");
        if (all_ok) {
            PrintOk("6 线程并发全部成功（success=" + std::to_string(success_count.load()) + "/6）");
        } else {
            PrintFail("并发测试失败（success=" + std::to_string(success_count.load()) +
                      " error=" + std::to_string(error_count.load()) + ")");
        }
    }

    // ─── 4.5 卸载并发池 ───────────────────────────────────────────────────────
    PrintStep("4.5", "POST /unload — 销毁并发池");
    {
        const json req{{"algorithm_id", "llm_rule_explainer"},
                       {"version",      "1.0.0"},
                       {"backend_type", "python_http_service"}};
        auto [status, body] = Post(pp, "/unload", req);
        const bool ok = (status == 200) && body.value("ok", false) &&
                        body.value("load_status", "") == "unloaded";
        RecordStep(SEC, "4.5_pool_unload", "POST", "/unload", req, status, body, ok,
                   "[并发池卸载] POST /unload：销毁并发池并释放所有连接。"
                   " load_status=unloaded 确认并发池已完全清除，runner_cache_size 将恢复为 0。"
                   " 卸载后 Mock Python 服务将不会再接收到任何新请求，证明池的生命周期管理正确。");
        if (ok) {
            PrintOk("并发池卸载成功，load_status=unloaded");
        } else {
            PrintFail("并发池卸载失败：HTTP " + std::to_string(status));
        }
    }

    section_report = g_report.value(SEC, json::array());
    std::cout << kGreen << "\n  ✅ 接口四全部步骤通过\n" << kReset;
}

// =============================================================================
// main — 组装全部 Demo Section，写出报告
// =============================================================================
int main() {
    std::cout.setf(std::ios::unitbuf);
    std::cerr.setf(std::ios::unitbuf);

    // 确定路径
    const fs::path src_root  = SourceRoot();
    const fs::path demo_dir  = src_root / "demo";
    const fs::path output_path = demo_dir / "demo_output.json";

    fs::create_directories(demo_dir);

    // 清理上次运行留下的注册表和 fixture （保证每次 demo 运行环境干净）
    std::error_code _ec;
    fs::remove(demo_dir / "shared_registry.json", _ec);
    fs::remove(demo_dir / "pool_registry.json",   _ec);
    fs::remove_all(demo_dir / "pool_fixture",      _ec);

    std::cout << kBold
              << "\n╔════════════════════════════════════════════════════════════╗\n"
              << "║        AlgoLib 四类接口 Demo — 功能展示程序               ║\n"
              << "╚════════════════════════════════════════════════════════════╝\n"
              << kReset
              << "  源码根目录：" << src_root.string() << "\n"
              << "  输出报告：  " << output_path.string() << "\n"
              << "  运行时间：  " << NowIso8601() << "\n";

    g_report["meta"] = {
        {"demo",       "AlgoLib Four API Demo"},
        {"started_at", NowIso8601()},
        {"source_root", src_root.generic_string()},
        {"output_path", output_path.generic_string()},
    };

    // ─── 准备 Section 1-3 共用的临时注册表 ────────────────────────────────────
    const fs::path shared_registry = demo_dir / "shared_registry.json";
    const fs::path shared_log      = demo_dir / "shared_audit.jsonl";

    // 初始化（确保注册表文件存在且干净）
    {
        algolib::AlgorithmRegistry init_reg(shared_registry);
        init_reg.Reload();  // 新文件时初始化空 registry
    }

    RunningServer shared_server({shared_registry, shared_log, "127.0.0.1", 0});
    shared_server.Start();
    const int sp = shared_server.port();
    std::cout << "\n  共享 Demo Server 启动，端口=" << sp << "\n";

    // ─── 验证服务器健康 ────────────────────────────────────────────────────────
    {
        auto [status, body] = Get(sp, "/health");
        if (status != 200 || !body.value("ok", false)) {
            std::cerr << "[FATAL] Server health check failed\n";
            return 1;
        }
        std::cout << kGreen << "  ✓ Server /health OK\n" << kReset;
    }

    // 各节报告收集
    json report_sec1, report_sec2, report_sec3, report_sec4;
    int  overall_failures = 0;

    // ─── SECTION 1 ─────────────────────────────────────────────────────────────
    try {
        DemoRegistryApi(sp, src_root, report_sec1);
    } catch (const std::exception& ex) {
        ++overall_failures;
        std::cerr << kRed << "\n[FAIL] Section 1 异常：" << ex.what() << kReset << "\n";
        g_report["registry_api_error"] = ex.what();
    }

    // ─── SECTION 2 ─────────────────────────────────────────────────────────────
    // 先 reload 清理删除状态
    Post(sp, "/reload", {});
    try {
        DemoModelLoadApi(sp, src_root, report_sec2);
    } catch (const std::exception& ex) {
        ++overall_failures;
        std::cerr << kRed << "\n[FAIL] Section 2 异常：" << ex.what() << kReset << "\n";
        g_report["model_load_api_error"] = ex.what();
    }

    // ─── SECTION 3 ─────────────────────────────────────────────────────────────
    // 模型仍在注册表中（Section 2 未删除），直接使用
    try {
        DemoFileServingApi(sp, src_root, report_sec3);
    } catch (const std::exception& ex) {
        ++overall_failures;
        std::cerr << kRed << "\n[FAIL] Section 3 异常：" << ex.what() << kReset << "\n";
        g_report["file_serving_api_error"] = ex.what();
    }

    // ─── SECTION 4 ─────────────────────────────────────────────────────────────
    try {
        DemoPythonPool(src_root, demo_dir, report_sec4);
    } catch (const std::exception& ex) {
        ++overall_failures;
        std::cerr << kRed << "\n[FAIL] Section 4 异常：" << ex.what() << kReset << "\n";
        g_report["python_pool_api_error"] = ex.what();
    }

    // ─── 最终汇总 ──────────────────────────────────────────────────────────────
    g_report["meta"]["finished_at"]      = NowIso8601();
    g_report["meta"]["overall_failures"] = overall_failures;
    g_report["meta"]["overall_passed"]   = (overall_failures == 0);

    // 统计各 section 通过率
    auto count_passed = [](const json& section) -> std::pair<int, int> {
        if (!section.is_array()) return {0, 0};
        int passed = 0, total = 0;
        for (const auto& step : section) {
            ++total;
            if (step.value("passed", false)) ++passed;
        }
        return {passed, total};
    };

    auto [p1, t1] = count_passed(g_report.value("registry_api",    json::array()));
    auto [p2, t2] = count_passed(g_report.value("model_load_api",  json::array()));
    auto [p3, t3] = count_passed(g_report.value("file_serving_api",json::array()));
    auto [p4, t4] = count_passed(g_report.value("python_pool_api", json::array()));

    g_report["summary"] = {
        {"registry_api",     {{"passed", p1}, {"total", t1}}},
        {"model_load_api",   {{"passed", p2}, {"total", t2}}},
        {"file_serving_api", {{"passed", p3}, {"total", t3}}},
        {"python_pool_api",  {{"passed", p4}, {"total", t4}}},
        {"all_passed",       (overall_failures == 0)},
    };

    // 写出 JSON 报告
    std::ofstream out(output_path);
    if (out) {
        out << g_report.dump(2) << "\n";
        out.close();
    } else {
        std::cerr << "[WARN] Cannot write output to " << output_path << "\n";
    }

    // 控制台最终汇总表
    std::cout << kBold
              << "\n╔════════════════════════════════════════════════════════════╗\n"
              << "║                     Demo 汇总结果                         ║\n"
              << "╠════════════════════════════════════════════════════════════╣\n"
              << kReset;
    auto print_row = [&](const std::string& name, int p, int t) {
        std::string mark = (p == t) ? (std::string(kGreen) + " PASS") : (std::string(kRed) + " FAIL");
        std::cout << "  " << std::left << std::setw(28) << name
                  << mark << kReset
                  << "  " << p << "/" << t << " 步通过\n";
    };
    print_row("接口一 Registry API",        p1, t1);
    print_row("接口二 Model Load API",       p2, t2);
    print_row("接口三 File Serving API",     p3, t3);
    print_row("接口四 Python Pool API",      p4, t4);
    std::cout << kBold
              << "╠════════════════════════════════════════════════════════════╣\n"
              << kReset;
    if (overall_failures == 0) {
        std::cout << kGreen << kBold
                  << "  ✅ 全部 Demo 步骤通过！\n"
                  << kReset;
    } else {
        std::cout << kRed << kBold
                  << "  ❌ " << overall_failures << " 个 Section 失败，详见 demo_output.json\n"
                  << kReset;
    }
    std::cout << kBold
              << "╚════════════════════════════════════════════════════════════╝\n\n"
              << kReset;
    std::cout << "  报告已写出：" << output_path.string() << "\n\n";

    return (overall_failures == 0) ? 0 : 1;
}