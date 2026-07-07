#include "algolib/io/http_client.h"

#include <chrono>
#include <memory>
#include <regex>
#include <string>

#include <httplib.h>

#include "algolib/io/json_utils.h"

namespace algolib {
namespace {

struct ParsedUrl {
    std::string scheme;
    std::string host;
    int port = 0;
    std::string target;
};

// 中文注释：当前 Phase 3 先聚焦明文 http，若后续需要 https 可在此基础上扩展。
Result<ParsedUrl> ParseHttpUrl(const std::string& url) {
    static const std::regex kUrlPattern(
        R"(^([A-Za-z][A-Za-z0-9+\.-]*)://([^/:?#]+)(?::([0-9]+))?([^?#]*)?(\?[^#]*)?$)");

    std::smatch match;
    if (!std::regex_match(url, match, kUrlPattern)) {
        return Status::Error(ErrorCode::kInvalidArgument,
                             "Invalid service URL: " + url);
    }

    ParsedUrl parsed;
    parsed.scheme = match[1].str();
    parsed.host = match[2].str();
    if (parsed.scheme != "http") {
        return Status::Error(
            ErrorCode::kInvalidArgument,
            "Only http service endpoints are supported in the current build: " + url);
    }

    parsed.port = match[3].matched ? std::stoi(match[3].str()) : 80;
    const std::string path = match[4].matched && !match[4].str().empty() ? match[4].str() : "/";
    const std::string query = match[5].matched ? match[5].str() : "";
    parsed.target = path + query;
    return parsed;
}

Status MapTransportError(httplib::Error error, const std::string& url) {
    switch (error) {
        case httplib::Error::ConnectionTimeout:
        case httplib::Error::Read:
        case httplib::Error::Write:
            return Status::Error(
                ErrorCode::kServiceTimeout,
                "HTTP request timed out for " + url + " (transport error " +
                    std::to_string(static_cast<int>(error)) + ").");
        default:
            return Status::Error(
                ErrorCode::kServiceUnavailable,
                "HTTP request failed for " + url + " (transport error " +
                    std::to_string(static_cast<int>(error)) + ").");
    }
}

void ConfigureTimeouts(httplib::Client* client, int timeout_ms) {
    const auto timeout = std::chrono::milliseconds(timeout_ms);
    client->set_connection_timeout(timeout);
    client->set_read_timeout(timeout);
    client->set_write_timeout(timeout);
    client->set_follow_location(false);
    // 中文注释：当前每次 HTTP 调用都会新建一个 client，
    // 显式关闭 keep-alive 可以减少测试与短生命周期服务的连接残留。
    client->set_keep_alive(false);
}

}  // namespace

Result<HttpResponse> HttpClient::Get(const std::string& url, int timeout_ms) const {
    auto parsed_result = ParseHttpUrl(url);
    if (!parsed_result.ok()) {
        return parsed_result.status();
    }

    const ParsedUrl& parsed = parsed_result.value();
    auto client = std::make_unique<httplib::Client>(parsed.host, parsed.port);
    ConfigureTimeouts(client.get(), timeout_ms);

    auto response = client->Get(parsed.target.c_str());
    if (!response) {
        return MapTransportError(response.error(), url);
    }

    return HttpResponse{response->status, response->body};
}

Result<HttpResponse> HttpClient::PostJson(const std::string& url,
                                          const nlohmann::json& body_json,
                                          int timeout_ms) const {
    auto parsed_result = ParseHttpUrl(url);
    if (!parsed_result.ok()) {
        return parsed_result.status();
    }

    const ParsedUrl& parsed = parsed_result.value();
    auto client = std::make_unique<httplib::Client>(parsed.host, parsed.port);
    ConfigureTimeouts(client.get(), timeout_ms);

    auto response =
        client->Post(parsed.target.c_str(), JsonUtils::Dump(body_json), "application/json");
    if (!response) {
        return MapTransportError(response.error(), url);
    }

    return HttpResponse{response->status, response->body};
}

}  // namespace algolib
