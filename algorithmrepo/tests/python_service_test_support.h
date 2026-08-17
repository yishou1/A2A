#pragma once

#include <chrono>
#include <atomic>
#include <iostream>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

#include <httplib.h>
#include <nlohmann/json.hpp>

#include "algolib/io/json_utils.h"

namespace algolib::testsupport {

namespace fs = std::filesystem;

inline fs::path SourceRoot() {
    return fs::path(ALGOLIB_SOURCE_DIR);
}

inline std::string ReadTextFile(const fs::path& file_path) {
    std::ifstream input_stream(file_path, std::ios::in | std::ios::binary);
    if (!input_stream.is_open()) {
        throw std::runtime_error("Test fixture file should be readable: " +
                                 file_path.string());
    }
    return std::string(std::istreambuf_iterator<char>(input_stream),
                       std::istreambuf_iterator<char>());
}

inline void WriteTextFile(const fs::path& file_path, const std::string& content) {
    fs::create_directories(file_path.parent_path());
    std::ofstream output_stream(file_path, std::ios::out | std::ios::binary | std::ios::trunc);
    if (!output_stream.is_open()) {
        throw std::runtime_error("Test fixture file should be writable: " +
                                 file_path.string());
    }
    output_stream << content;
}

inline void ReplaceAll(std::string* content,
                       const std::string& from,
                       const std::string& to) {
    std::size_t start_pos = 0;
    while ((start_pos = content->find(from, start_pos)) != std::string::npos) {
        content->replace(start_pos, from.length(), to);
        start_pos += to.length();
    }
}

inline fs::path CreateServiceFixtureWithIdentity(const fs::path& temp_dir,
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

inline void PointServiceFixtureAtBaseUrl(const fs::path& fixture_dir,
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

struct MockPythonServiceConfig {
    std::string algorithm_id = "llm_rule_explainer";
    std::string version = "1.0.0";
    int fixed_port = 0;
    int health_http_status = 200;
    int metadata_http_status = 200;
    int predict_http_status = 200;
    int predict_delay_ms = 0;
    int predict_http_status_after_first = -1;
    nlohmann::json health_body;
    nlohmann::json metadata_body;
    nlohmann::json predict_body;
    nlohmann::json predict_body_after_first;
};

// 中文注释：测试内嵌 mock service，避免依赖外部 Python 进程或固定端口。
class MockPythonService {
public:
    MockPythonService() : MockPythonService(MockPythonServiceConfig{}) {}

    explicit MockPythonService(MockPythonServiceConfig config)
        : config_(std::move(config)) {
        // 中文注释：把 mock server 调成更短的连接生命周期，
        // 避免失败路径测试结束时因 keep-alive 连接残留而拖慢 stop/join。
        server_.set_keep_alive_max_count(1);
        server_.set_keep_alive_timeout(1);
        server_.set_read_timeout(std::chrono::milliseconds(200));
        server_.set_write_timeout(std::chrono::milliseconds(200));
        server_.set_idle_interval(std::chrono::milliseconds(100));

        server_.Get("/health", [this](const httplib::Request&, httplib::Response& response) {
            std::cerr << "[TRACE] MockPythonService /health" << '\n';
            response.status = config_.health_http_status;
            response.set_content(algolib::JsonUtils::Dump(DefaultHealthBody(), 2),
                                 "application/json");
        });

        server_.Get("/metadata", [this](const httplib::Request&, httplib::Response& response) {
            std::cerr << "[TRACE] MockPythonService /metadata" << '\n';
            response.status = config_.metadata_http_status;
            response.set_content(algolib::JsonUtils::Dump(DefaultMetadataBody(), 2),
                                 "application/json");
        });

        server_.Post("/predict",
                     [this](const httplib::Request& request, httplib::Response& response) {
                         const int predict_call_index =
                             predict_call_count_.fetch_add(1, std::memory_order_relaxed);
                         std::cerr << "[TRACE] MockPythonService /predict call "
                                   << predict_call_index << '\n';
                         if (config_.predict_delay_ms > 0) {
                             std::this_thread::sleep_for(
                                 std::chrono::milliseconds(config_.predict_delay_ms));
                         }
                         const int override_status =
                             predict_http_status_override_.load(std::memory_order_relaxed);
                         response.status = override_status >= 0
                                               ? override_status
                                               : (predict_call_index == 0 ||
                                                          config_.predict_http_status_after_first <
                                                              0
                                                      ? config_.predict_http_status
                                                      : config_.predict_http_status_after_first);
                         std::cerr << "[TRACE] MockPythonService /predict status "
                                   << response.status << '\n';
                         response.set_content(algolib::JsonUtils::Dump(
                                                  DefaultPredictBody(request.body, predict_call_index),
                                                  2),
                                              "application/json");
                     });

        if (config_.fixed_port > 0) {
            bool bound = false;
            for (int attempt = 0; attempt < 10 && !bound; ++attempt) {
                bound = server_.bind_to_port("127.0.0.1", config_.fixed_port);
                if (!bound) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(20));
                }
            }
            if (!bound) {
                throw std::runtime_error("MockPythonService failed to bind to the requested port.");
            }
            port_ = config_.fixed_port;
        } else {
            port_ = server_.bind_to_any_port("127.0.0.1");
        }
        if (port_ <= 0) {
            throw std::runtime_error("MockPythonService failed to bind to an ephemeral port.");
        }

        server_thread_ = std::thread([this]() {
            server_.listen_after_bind();
        });

        WaitUntilReady();
    }

    ~MockPythonService() {
        server_.stop();
        if (server_thread_.joinable()) {
            server_thread_.join();
        }
    }

    std::string base_url() const {
        return "http://127.0.0.1:" + std::to_string(port_);
    }

    int port() const {
        return port_;
    }

    // 中文注释：允许测试在注册完成后动态切换 predict 返回码，
    // 用来覆盖运行期失败路径，而不影响注册阶段的联调校验。
    void SetPredictHttpStatus(int status_code) {
        predict_http_status_override_.store(status_code, std::memory_order_relaxed);
    }

    void ClearPredictHttpStatusOverride() {
        predict_http_status_override_.store(-1, std::memory_order_relaxed);
    }

private:
    void WaitUntilReady() {
        // 中文注释：主动轮询 /metadata，确保 mock service 真正开始监听后再交给测试使用。
        // 这里避开 /health，是为了不干扰故意构造 health=503 的失败路径测试。
        httplib::Client client("127.0.0.1", port_);
        client.set_connection_timeout(std::chrono::milliseconds(200));
        client.set_read_timeout(std::chrono::milliseconds(200));
        client.set_write_timeout(std::chrono::milliseconds(200));
        client.set_keep_alive(false);

        for (int attempt = 0; attempt < 40; ++attempt) {
            auto response = client.Get("/metadata");
            if (response) {
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }

        throw std::runtime_error("MockPythonService did not become ready in time.");
    }

    nlohmann::json DefaultHealthBody() const {
        if (!config_.health_body.is_null()) {
            return config_.health_body;
        }
        return {
            {"ok", true},
            {"status", "ready"},
            {"algorithm_id", config_.algorithm_id},
            {"version", config_.version},
            {"model_loaded", true},
        };
    }

    nlohmann::json DefaultMetadataBody() const {
        if (!config_.metadata_body.is_null()) {
            return config_.metadata_body;
        }
        return {
            {"algorithm_id", config_.algorithm_id},
            {"version", config_.version},
            {"backend_type", "python_http_service"},
            {"task_family", "generation"},
            {"input_schema_version", "1.0.0"},
            {"output_schema_version", "1.0.0"},
            {"batch_supported", false},
            {"streaming_supported", false},
            {"max_input_chars", 20000},
            {"timeout_ms_recommended", 10000},
        };
    }

    nlohmann::json DefaultPredictBody(const std::string& request_body,
                                      int predict_call_index) const {
        if (predict_call_index > 0 && !config_.predict_body_after_first.is_null()) {
            return config_.predict_body_after_first;
        }
        if (!config_.predict_body.is_null()) {
            return config_.predict_body;
        }

        nlohmann::json request_json =
            nlohmann::json::parse(request_body, nullptr, false);
        const std::string request_id =
            request_json.is_object() ? request_json.value("request_id", "req_mock")
                                     : "req_mock";
        const std::string trace_id =
            request_json.is_object() ? request_json.value("trace_id", "trace_mock")
                                     : "trace_mock";

        return {
            {"ok", true},
            {"request_id", request_id},
            {"trace_id", trace_id},
            {"algorithm_id", config_.algorithm_id},
            {"version", config_.version},
            {"outputs",
             {
                 {"explanation",
                  "Required fields are missing and manual review is recommended."},
                 {"confidence", 0.82},
             }},
            {"usage", {{"latency_ms", 120}}},
            {"error", nullptr},
        };
    }

    MockPythonServiceConfig config_;
    httplib::Server server_;
    int port_ = 0;
    std::thread server_thread_;
    std::atomic<int> predict_call_count_{0};
    std::atomic<int> predict_http_status_override_{-1};
};

}  // namespace algolib::testsupport
