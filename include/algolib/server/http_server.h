#pragma once

#include <filesystem>
#include <cstddef>
#include <memory>
#include <string>

namespace algolib {

struct HttpServerConfig {
    std::filesystem::path registry_path;
    std::filesystem::path execution_log_path;
    std::string host = "127.0.0.1";
    int port = 8088;
    std::filesystem::path operational_function_catalog_path;
    // 0 使用 cpp-httplib 默认线程数；正数时显式限制 HTTP Handler 工作线程。
    std::size_t worker_threads = 0;
    // 0 表示不限制排队请求；生产环境建议设置有界值形成背压。
    std::size_t max_queued_requests = 0;
};

// 中文注释：AlgolibHttpServer 是算法库的常驻 HTTP 入口，复用现有 registry 与 runtime。
class AlgolibHttpServer {
public:
    explicit AlgolibHttpServer(HttpServerConfig config);
    ~AlgolibHttpServer();

    AlgolibHttpServer(const AlgolibHttpServer&) = delete;
    AlgolibHttpServer& operator=(const AlgolibHttpServer&) = delete;

    bool Listen();
    bool Listen(const std::string& host, int port);
    int BindToAnyPort(const std::string& host);
    bool ListenAfterBind();
    void Stop();
    bool IsRunning() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace algolib
