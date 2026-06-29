#include "algolib/server/http_server.h"

#include <algorithm>
#include <cctype>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <string>
#include <utility>

#include <httplib.h>
#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/algorithm_key.h"
#include "algolib/core/backend_type.h"
#include "algolib/core/error_code.h"
#include "algolib/core/status.h"
#include "algolib/io/json_utils.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"
#include "algolib/runtime/execution_coordinator.h"

namespace algolib {
namespace {

using json = nlohmann::json;

HttpServerConfig NormalizeConfig(HttpServerConfig config) {
    if (config.registry_path.empty()) {
        config.registry_path = std::filesystem::current_path() / ".algolib" / "registry.json";
    }
    if (config.host.empty()) {
        config.host = "127.0.0.1";
    }
    if (config.port <= 0) {
        config.port = 8088;
    }
    return config;
}

std::string ToLowerAscii(std::string value) {
    for (char& ch : value) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return value;
}

json ErrorPayload(const Status& status) {
    return json{
        {"ok", false},
        {"error_code", ToString(status.code())},
        {"message", status.message()},
    };
}

Status InvalidArgument(std::string message) {
    return Status::Error(ErrorCode::kInvalidArgument, std::move(message));
}

int HttpStatusForErrorCode(ErrorCode code) {
    switch (code) {
        case ErrorCode::kOk:
            return 200;
        case ErrorCode::kAlgorithmNotFound:
            return 404;
        case ErrorCode::kRegistryConflict:
            return 409;
        case ErrorCode::kAlgorithmNotActive:
        case ErrorCode::kStatusTransitionInvalid:
        case ErrorCode::kBackendTypeMismatch:
            return 409;
        case ErrorCode::kInputSchemaInvalid:
        case ErrorCode::kOutputSchemaInvalid:
        case ErrorCode::kServiceOutputSchemaInvalid:
            return 422;
        case ErrorCode::kInvalidArgument:
        case ErrorCode::kInvalidAlgorithmCard:
        case ErrorCode::kUnsupportedBackendType:
        case ErrorCode::kMissingRequiredField:
        case ErrorCode::kMissingRequiredFile:
        case ErrorCode::kYamlParseError:
        case ErrorCode::kJsonParseError:
            return 400;
        case ErrorCode::kServiceTimeout:
            return 504;
        case ErrorCode::kServiceNotReady:
        case ErrorCode::kServiceUnavailable:
            return 503;
        case ErrorCode::kServiceHttpError:
        case ErrorCode::kServiceMetadataMismatch:
        case ErrorCode::kServiceResponseInvalid:
        case ErrorCode::kOnnxLoadFailed:
        case ErrorCode::kOnnxRuntimeError:
        case ErrorCode::kOnnxInputTensorMismatch:
        case ErrorCode::kOnnxOutputTensorMismatch:
        case ErrorCode::kPreprocessFailed:
        case ErrorCode::kPostprocessFailed:
        case ErrorCode::kTokenizerNotSupported:
        case ErrorCode::kGoldenCaseFailed:
            return 502;
        case ErrorCode::kOnnxModelNotFound:
        case ErrorCode::kIoError:
        case ErrorCode::kRegistryStoreError:
            return 500;
    }
    return 500;
}

int HttpStatusForStatus(const Status& status) {
    return status.ok() ? 200 : HttpStatusForErrorCode(status.code());
}

int HttpStatusForRunError(const std::string& code) {
    if (code == "ALGORITHM_NOT_FOUND") {
        return 404;
    }
    if (code == "ALGORITHM_NOT_ACTIVE" || code == "STATUS_TRANSITION_INVALID" ||
        code == "BACKEND_TYPE_MISMATCH" || code == "REGISTRY_CONFLICT") {
        return 409;
    }
    if (code == "INPUT_SCHEMA_INVALID" || code == "OUTPUT_SCHEMA_INVALID" ||
        code == "SERVICE_OUTPUT_SCHEMA_INVALID") {
        return 422;
    }
    if (code == "INVALID_ARGUMENT" || code == "INVALID_ALGORITHM_CARD" ||
        code == "UNSUPPORTED_BACKEND_TYPE" || code == "MISSING_REQUIRED_FIELD" ||
        code == "MISSING_REQUIRED_FILE" || code == "YAML_PARSE_ERROR" ||
        code == "JSON_PARSE_ERROR") {
        return 400;
    }
    if (code == "SERVICE_TIMEOUT") {
        return 504;
    }
    if (code == "SERVICE_NOT_READY" || code == "SERVICE_UNAVAILABLE") {
        return 503;
    }
    return 502;
}

void WriteJson(httplib::Response* response, int status_code, const json& payload) {
    response->status = status_code;
    response->set_header("Cache-Control", "no-store");
    response->set_content(JsonUtils::Dump(payload), "application/json; charset=utf-8");
}

Result<json> ParseJsonBody(const httplib::Request& request) {
    if (request.body.empty()) {
        return InvalidArgument("HTTP request body must contain a JSON object.");
    }
    try {
        return json::parse(request.body);
    } catch (const std::exception& ex) {
        return Status::Error(ErrorCode::kJsonParseError,
                             std::string("Failed to parse HTTP JSON body: ") + ex.what());
    }
}

Result<AlgorithmKey> ParseKey(const std::string& algorithm_id,
                              const std::string& version,
                              const std::string& backend_type) {
    auto backend_result = ParseBackendType(backend_type);
    if (!backend_result.ok()) {
        return backend_result.status();
    }
    if (algorithm_id.empty() || version.empty()) {
        return InvalidArgument("algorithm_id and version must be non-empty.");
    }
    return AlgorithmKey{algorithm_id, version, backend_result.value()};
}

Result<AlgorithmKey> ParseKeyFromMatches(const httplib::Request& request) {
    if (request.matches.size() < 4) {
        return InvalidArgument("Algorithm URL must contain algorithm_id, version and backend_type.");
    }
    return ParseKey(request.matches[1].str(), request.matches[2].str(),
                    request.matches[3].str());
}

bool ParseActiveOnly(const httplib::Request& request) {
    if (!request.has_param("active_only")) {
        return true;
    }
    const std::string value = ToLowerAscii(request.get_param_value("active_only"));
    return !(value == "false" || value == "0" || value == "no");
}

json EntryPayload(const AlgorithmEntry& entry) {
    return json{
        {"ok", true},
        {"entry", ToJson(entry)},
        {"agent_view", ToAgentViewJson(entry)},
    };
}

json EntrySummaryPayload(const AlgorithmEntry& entry) {
    return json{
        {"ok", true},
        {"algorithm_id", entry.key.algorithm_id},
        {"version", entry.key.version},
        {"backend_type", ToString(entry.key.backend_type)},
        {"status", ToString(entry.status)},
        {"agent_view", ToAgentViewJson(entry)},
    };
}

}  // namespace

class AlgolibHttpServer::Impl {
public:
    explicit Impl(HttpServerConfig config)
        : config_(NormalizeConfig(std::move(config))), registry_(config_.registry_path) {
        RegisterRoutes();
    }

    bool Listen() {
        return Listen(config_.host, config_.port);
    }

    bool Listen(const std::string& host, int port) {
        config_.host = host.empty() ? "127.0.0.1" : host;
        config_.port = port <= 0 ? 8088 : port;
        auto reload_status = ReloadRegistry();
        if (!reload_status.ok()) {
            startup_error_ = reload_status;
            return false;
        }
        return server_.listen(config_.host, config_.port);
    }

    int BindToAnyPort(const std::string& host) {
        config_.host = host.empty() ? "127.0.0.1" : host;
        auto reload_status = ReloadRegistry();
        if (!reload_status.ok()) {
            startup_error_ = reload_status;
            return -1;
        }
        const int port = server_.bind_to_any_port(config_.host);
        if (port > 0) {
            config_.port = port;
        }
        return port;
    }

    bool ListenAfterBind() {
        return server_.listen_after_bind();
    }

    void Stop() {
        server_.stop();
    }

    bool IsRunning() const {
        return server_.is_running();
    }

private:
    Status ReloadRegistry() {
        std::lock_guard<std::mutex> lock(mutex_);
        return registry_.Reload();
    }

    Status ReloadRegistryLocked() {
        return registry_.Reload();
    }

    void RegisterRoutes() {
        server_.Get("/health", [this](const httplib::Request&, httplib::Response& response) {
            const json payload{
                {"ok", true},
                {"status", "ready"},
                {"registry_path", config_.registry_path.generic_string()},
                {"execution_log_path", config_.execution_log_path.generic_string()},
            };
            WriteJson(&response, 200, payload);
        });

        server_.Post("/reload", [this](const httplib::Request&, httplib::Response& response) {
            std::lock_guard<std::mutex> lock(mutex_);
            const Status status = ReloadRegistryLocked();
            if (!status.ok()) {
                WriteJson(&response, HttpStatusForStatus(status), ErrorPayload(status));
                return;
            }
            WriteJson(&response, 200, json{{"ok", true}, {"status", "reloaded"}});
        });

        server_.Get("/algorithms", [this](const httplib::Request& request,
                                           httplib::Response& response) {
            std::lock_guard<std::mutex> lock(mutex_);
            const Status reload_status = ReloadRegistryLocked();
            if (!reload_status.ok()) {
                WriteJson(&response, HttpStatusForStatus(reload_status),
                          ErrorPayload(reload_status));
                return;
            }

            json algorithms = json::array();
            for (const auto& view : registry_.ListAgentViews(ParseActiveOnly(request))) {
                algorithms.push_back(view);
            }

            WriteJson(&response, 200,
                      json{{"ok", true},
                           {"count", algorithms.size()},
                           {"active_only", ParseActiveOnly(request)},
                           {"algorithms", algorithms}});
        });

        server_.Get(R"(/algorithms/([^/]+)/([^/]+)/([^/]+))",
                    [this](const httplib::Request& request, httplib::Response& response) {
                        auto key_result = ParseKeyFromMatches(request);
                        if (!key_result.ok()) {
                            WriteJson(&response, HttpStatusForStatus(key_result.status()),
                                      ErrorPayload(key_result.status()));
                            return;
                        }

                        std::lock_guard<std::mutex> lock(mutex_);
                        const Status reload_status = ReloadRegistryLocked();
                        if (!reload_status.ok()) {
                            WriteJson(&response, HttpStatusForStatus(reload_status),
                                      ErrorPayload(reload_status));
                            return;
                        }

                        auto entry_result = registry_.Get(key_result.value());
                        if (!entry_result.ok()) {
                            WriteJson(&response, HttpStatusForStatus(entry_result.status()),
                                      ErrorPayload(entry_result.status()));
                            return;
                        }
                        WriteJson(&response, 200, EntryPayload(entry_result.value()));
                    });

        server_.Post("/algorithms/register",
                     [this](const httplib::Request& request, httplib::Response& response) {
                         auto body_result = ParseJsonBody(request);
                         if (!body_result.ok()) {
                             WriteJson(&response, HttpStatusForStatus(body_result.status()),
                                       ErrorPayload(body_result.status()));
                             return;
                         }

                         const json& body = body_result.value();
                         if (!body.is_object() || !body.contains("package_or_card_path") ||
                             !body.at("package_or_card_path").is_string() ||
                             body.at("package_or_card_path").get<std::string>().empty()) {
                             const Status status =
                                 InvalidArgument("Body must contain non-empty string "
                                                 "package_or_card_path.");
                             WriteJson(&response, HttpStatusForStatus(status),
                                       ErrorPayload(status));
                             return;
                         }

                         std::lock_guard<std::mutex> lock(mutex_);
                         const Status reload_status = ReloadRegistryLocked();
                         if (!reload_status.ok()) {
                             WriteJson(&response, HttpStatusForStatus(reload_status),
                                       ErrorPayload(reload_status));
                             return;
                         }

                         auto register_result = registry_.Register(
                             body.at("package_or_card_path").get<std::string>());
                         if (!register_result.ok()) {
                             WriteJson(&response, HttpStatusForStatus(register_result.status()),
                                       ErrorPayload(register_result.status()));
                             return;
                         }
                         WriteJson(&response, 201, EntrySummaryPayload(register_result.value()));
                     });

        RegisterLifecycleRoute("validate", [this](const AlgorithmKey& key) {
            return registry_.Validate(key);
        });
        RegisterLifecycleRoute("activate", [this](const AlgorithmKey& key) {
            return registry_.Activate(key);
        });
        RegisterLifecycleRoute("disable", [this](const AlgorithmKey& key) {
            return registry_.Disable(key);
        });

        server_.Delete(R"(/algorithms/([^/]+)/([^/]+)/([^/]+))",
                       [this](const httplib::Request& request, httplib::Response& response) {
                           auto key_result = ParseKeyFromMatches(request);
                           if (!key_result.ok()) {
                               WriteJson(&response, HttpStatusForStatus(key_result.status()),
                                         ErrorPayload(key_result.status()));
                               return;
                           }

                           std::lock_guard<std::mutex> lock(mutex_);
                           const Status reload_status = ReloadRegistryLocked();
                           if (!reload_status.ok()) {
                               WriteJson(&response, HttpStatusForStatus(reload_status),
                                         ErrorPayload(reload_status));
                               return;
                           }

                           auto delete_result = registry_.Delete(key_result.value());
                           if (!delete_result.ok()) {
                               WriteJson(&response, HttpStatusForStatus(delete_result.status()),
                                         ErrorPayload(delete_result.status()));
                               return;
                           }
                           WriteJson(&response, 200, EntrySummaryPayload(delete_result.value()));
                       });

        server_.Post("/run", [this](const httplib::Request& request,
                                     httplib::Response& response) {
            auto body_result = ParseJsonBody(request);
            if (!body_result.ok()) {
                WriteJson(&response, HttpStatusForStatus(body_result.status()),
                          ErrorPayload(body_result.status()));
                return;
            }

            auto request_result = AlgorithmRequestFromJson(body_result.value());
            if (!request_result.ok()) {
                WriteJson(&response, HttpStatusForStatus(request_result.status()),
                          ErrorPayload(request_result.status()));
                return;
            }

            std::lock_guard<std::mutex> lock(mutex_);
            const Status reload_status = ReloadRegistryLocked();
            if (!reload_status.ok()) {
                WriteJson(&response, HttpStatusForStatus(reload_status),
                          ErrorPayload(reload_status));
                return;
            }

            // 中文注释：先保持一次请求内 registry 与 runner 创建串行，后续可替换为共享锁和 runner 缓存。
            ExecutionCoordinator coordinator(registry_, config_.execution_log_path);
            const AlgorithmResult run_result = coordinator.Run(request_result.value());
            const json payload = ToJson(run_result);
            const int status_code = run_result.ok
                                        ? 200
                                        : HttpStatusForRunError(
                                              run_result.error.has_value()
                                                  ? run_result.error->code
                                                  : "UNKNOWN_ERROR");
            WriteJson(&response, status_code, payload);
        });

        server_.set_error_handler([](const httplib::Request&, httplib::Response& response) {
            if (response.status == 404) {
                const Status status =
                    Status::Error(ErrorCode::kAlgorithmNotFound,
                                  "HTTP endpoint not found.");
                WriteJson(&response, 404, ErrorPayload(status));
            }
        });

        server_.set_exception_handler(
            [](const httplib::Request&, httplib::Response& response, std::exception_ptr ep) {
                std::string message = "Unhandled server exception.";
                if (ep) {
                    try {
                        std::rethrow_exception(ep);
                    } catch (const std::exception& ex) {
                        message = ex.what();
                    }
                }
                const Status status = Status::Error(ErrorCode::kInvalidArgument, message);
                WriteJson(&response, 500, ErrorPayload(status));
            });
    }

    template <typename Handler>
    void RegisterLifecycleRoute(const std::string& action, Handler handler) {
        const std::string pattern = R"(/algorithms/([^/]+)/([^/]+)/([^/]+)/)" + action;
        server_.Post(pattern, [this, handler](const httplib::Request& request,
                                              httplib::Response& response) {
            auto key_result = ParseKeyFromMatches(request);
            if (!key_result.ok()) {
                WriteJson(&response, HttpStatusForStatus(key_result.status()),
                          ErrorPayload(key_result.status()));
                return;
            }

            std::lock_guard<std::mutex> lock(mutex_);
            const Status reload_status = ReloadRegistryLocked();
            if (!reload_status.ok()) {
                WriteJson(&response, HttpStatusForStatus(reload_status),
                          ErrorPayload(reload_status));
                return;
            }

            auto result = handler(key_result.value());
            if (!result.ok()) {
                WriteJson(&response, HttpStatusForStatus(result.status()),
                          ErrorPayload(result.status()));
                return;
            }
            WriteJson(&response, 200, EntrySummaryPayload(result.value()));
        });
    }

    HttpServerConfig config_;
    AlgorithmRegistry registry_;
    httplib::Server server_;
    mutable std::mutex mutex_;
    Status startup_error_ = Status::Ok();
};

AlgolibHttpServer::AlgolibHttpServer(HttpServerConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

AlgolibHttpServer::~AlgolibHttpServer() = default;

bool AlgolibHttpServer::Listen() {
    return impl_->Listen();
}

bool AlgolibHttpServer::Listen(const std::string& host, int port) {
    return impl_->Listen(host, port);
}

int AlgolibHttpServer::BindToAnyPort(const std::string& host) {
    return impl_->BindToAnyPort(host);
}

bool AlgolibHttpServer::ListenAfterBind() {
    return impl_->ListenAfterBind();
}

void AlgolibHttpServer::Stop() {
    impl_->Stop();
}

bool AlgolibHttpServer::IsRunning() const {
    return impl_->IsRunning();
}

}  // namespace algolib
