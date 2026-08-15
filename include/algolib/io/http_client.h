#pragma once

#include <filesystem>
#include <string>

#include <nlohmann/json.hpp>

#include "algolib/core/status.h"

namespace algolib {

struct HttpResponse {
    int status_code = 0;
    std::string body;
};

// 中文注释：HttpClient 提供 JSON over HTTP 和二进制文件下载能力。
class HttpClient {
public:
    Result<HttpResponse> Get(const std::string& url, int timeout_ms) const;

    Result<HttpResponse> PostJson(const std::string& url,
                                  const nlohmann::json& body_json,
                                  int timeout_ms) const;

    // 中文注释：DownloadFile — 以流式方式将 URL 指向的二进制内容写入本地 dest_path。
    // 若目标目录不存在则自动创建；已存在的文件会被覆盖。
    // timeout_ms=0 时使用默认超时（60s），大文件建议调大。
    Status DownloadFile(const std::string& url,
                        const std::filesystem::path& dest_path,
                        int timeout_ms = 60000) const;
};

}  // namespace algolib
