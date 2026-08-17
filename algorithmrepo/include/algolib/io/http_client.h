#pragma once

#include <string>

#include <nlohmann/json.hpp>

#include "algolib/core/status.h"

namespace algolib {

struct HttpResponse {
    int status_code = 0;
    std::string body;
};

// 中文注释：HttpClient 为 Phase 3 提供最小 JSON over HTTP 能力，后续 runner 也可复用。
class HttpClient {
public:
    Result<HttpResponse> Get(const std::string& url, int timeout_ms) const;

    Result<HttpResponse> PostJson(const std::string& url,
                                  const nlohmann::json& body_json,
                                  int timeout_ms) const;
};

}  // namespace algolib
