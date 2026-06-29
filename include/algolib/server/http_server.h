#pragma once

#include <filesystem>
#include <memory>
#include <string>

namespace algolib {

struct HttpServerConfig {
    std::filesystem::path registry_path;
    std::filesystem::path execution_log_path;
    std::string host = "127.0.0.1";
    int port = 8088;
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
