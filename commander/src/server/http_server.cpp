#include "algolib/server/http_server.h"

#include <algorithm>
#include <cctype>
#include <exception>
#include <filesystem>
#include <fstream>
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
#include "algolib/runtime/model_loader.h"
#include "algolib/runtime/runtime_factory.h"
#include "algolib/runtime/runtime_runner_cache.h"

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

// 中文注释：ParseQueryFilter — 从 HTTP 查询参数中构建 AlgorithmQueryFilter。
// 支持的参数：
//   active_only    (bool, 默认 true)  — 覆盖 status 过滤
//   status         (string)           — 仅在 active_only=false 时生效
//   task_family    (string)
//   backend        (string)           — onnx / python_http_service
//   capability     (string)
//   node           (string)           — node_id
//   max_vram       (int, MB)
//   max_memory     (int, MB)
//   max_cpu_cores  (int)
AlgorithmQueryFilter ParseQueryFilter(const httplib::Request& request) {
    AlgorithmQueryFilter f;

    // active_only
    f.active_only = ParseActiveOnly(request);

    // status（active_only=false 时才有意义）
    if (!f.active_only && request.has_param("status")) {
        auto parsed = ParseAlgorithmStatus(request.get_param_value("status"));
        if (parsed.ok()) {
            f.status = parsed.value();
        }
    }

    // task_family
    if (request.has_param("task_family")) {
        const std::string v = request.get_param_value("task_family");
        if (!v.empty()) {
            f.task_family = v;
        }
    }

    // backend (backend_type)
    if (request.has_param("backend")) {
        auto parsed = ParseBackendType(request.get_param_value("backend"));
        if (parsed.ok()) {
            f.backend_type = parsed.value();
        }
    }

    // capability
    if (request.has_param("capability")) {
        const std::string v = request.get_param_value("capability");
        if (!v.empty()) {
            f.capability = v;
        }
    }

    // node (node_id)
    if (request.has_param("node")) {
        const std::string v = request.get_param_value("node");
        if (!v.empty()) {
            f.node_id = v;
        }
    }

    // max_vram (MB)
    if (request.has_param("max_vram")) {
        try {
            f.max_vram_mb = std::stoi(request.get_param_value("max_vram"));
        } catch (...) {
            // 解析失败则忽略该参数
        }
    }

    // max_memory (MB)
    if (request.has_param("max_memory")) {
        try {
            f.max_memory_mb = std::stoi(request.get_param_value("max_memory"));
        } catch (...) {
        }
    }

    // max_cpu_cores
    if (request.has_param("max_cpu_cores")) {
        try {
            f.max_cpu_cores = std::stoi(request.get_param_value("max_cpu_cores"));
        } catch (...) {
        }
    }

    return f;
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
                {"runner_cache_size", runner_cache_.Size()},
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
            runner_cache_.Clear();
            WriteJson(&response, 200, json{{"ok", true}, {"status", "reloaded"}});
        });

        // 中文注释：GET /algorithms — 多维过滤查询算法列表
        // 查询参数（均可选）：
        //   active_only   (bool)   默认 true，false 时返回所有非 deleted 算法
        //   status        (string) draft/validated/active/disabled；仅 active_only=false 时生效
        //   task_family   (string) 精确匹配 task_family 字段
        //   backend       (string) onnx / python_http_service
        //   capability    (string) capabilities 列表中包含该值
        //   node          (string) deployments 中至少有一个 node_id 等于该值
        //   max_vram      (int)    单位 MB；筛选 min_vram_mb <= max_vram 的模型
        //   max_memory    (int)    单位 MB；筛选 min_memory_mb <= max_memory 的模型
        //   max_cpu_cores (int)    筛选 min_cpu_cores <= max_cpu_cores 的模型
        server_.Get("/algorithms", [this](const httplib::Request& request,
                                           httplib::Response& response) {
            std::lock_guard<std::mutex> lock(mutex_);
            const Status reload_status = ReloadRegistryLocked();
            if (!reload_status.ok()) {
                WriteJson(&response, HttpStatusForStatus(reload_status),
                          ErrorPayload(reload_status));
                return;
            }

            const AlgorithmQueryFilter filter = ParseQueryFilter(request);

            json algorithms = json::array();
            for (const auto& view : registry_.QueryAgentViews(filter)) {
                algorithms.push_back(view);
            }

            // 中文注释：在响应中回显生效的过滤条件，方便调用方调试。
            json applied_filter = json::object();
            applied_filter["active_only"] = filter.active_only;
            if (filter.status.has_value()) {
                applied_filter["status"] = ToString(filter.status.value());
            }
            if (filter.task_family.has_value()) {
                applied_filter["task_family"] = filter.task_family.value();
            }
            if (filter.backend_type.has_value()) {
                applied_filter["backend"] = ToString(filter.backend_type.value());
            }
            if (filter.capability.has_value()) {
                applied_filter["capability"] = filter.capability.value();
            }
            if (filter.node_id.has_value()) {
                applied_filter["node"] = filter.node_id.value();
            }
            if (filter.max_vram_mb.has_value()) {
                applied_filter["max_vram"] = filter.max_vram_mb.value();
            }
            if (filter.max_memory_mb.has_value()) {
                applied_filter["max_memory"] = filter.max_memory_mb.value();
            }
            if (filter.max_cpu_cores.has_value()) {
                applied_filter["max_cpu_cores"] = filter.max_cpu_cores.value();
            }

            WriteJson(&response, 200,
                      json{{"ok", true},
                           {"count", algorithms.size()},
                           {"filter", applied_filter},
                           {"algorithms", algorithms}});
        });

        // 中文注释：GET /algorithms/{id}/{version}/{backend}/files/{relative_path}
        // 文件服务端点：向远端节点暴露 package_root 下的模型文件（流式下载）。
        // 安全约束：
        //   1. relative_path 不允许包含 ".." 路径分量（防止目录穿越）。
        //   2. 只允许下载 package_root 下实际存在的普通文件。
        // 用途：远端节点在收到 http_pull 加载指令后，通过此接口拉取 .onnx 及配套文件。
        server_.Get(
            R"(/algorithms/([^/]+)/([^/]+)/([^/]+)/files/(.+))",
            [this](const httplib::Request& request, httplib::Response& response) {
                if (request.matches.size() < 5) {
                    const Status status =
                        InvalidArgument("URL must contain algorithm_id, version, "
                                        "backend_type and relative_path.");
                    WriteJson(&response, 400, ErrorPayload(status));
                    return;
                }

                auto key_result = ParseKeyFromMatches(request);
                if (!key_result.ok()) {
                    WriteJson(&response, HttpStatusForStatus(key_result.status()),
                              ErrorPayload(key_result.status()));
                    return;
                }
                const std::string relative_path_str = request.matches[4].str();

                // 中文注释：拒绝任何包含 ".." 分量的路径，防止目录穿越攻击。
                if (relative_path_str.find("..") != std::string::npos) {
                    const Status status =
                        InvalidArgument("relative_path must not contain '..'.");
                    WriteJson(&response, 400, ErrorPayload(status));
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

                const std::filesystem::path package_root =
                    entry_result.value().package_root;
                const std::filesystem::path target_file =
                    std::filesystem::weakly_canonical(package_root / relative_path_str);

                // 中文注释：确保解析后的绝对路径仍在 package_root 下（二次安全检查）。
                const std::string root_str =
                    std::filesystem::weakly_canonical(package_root).generic_string();
                const std::string file_str = target_file.generic_string();
                if (file_str.rfind(root_str, 0) != 0) {
                    const Status status =
                        InvalidArgument("relative_path escapes package_root.");
                    WriteJson(&response, 403, ErrorPayload(status));
                    return;
                }

                if (!std::filesystem::exists(target_file) ||
                    !std::filesystem::is_regular_file(target_file)) {
                    const Status status = Status::Error(
                        ErrorCode::kIoError,
                        "File not found: " + relative_path_str);
                    WriteJson(&response, 404, ErrorPayload(status));
                    return;
                }

                // 中文注释：以二进制流形式发送文件内容。
                std::ifstream file_stream(target_file,
                                          std::ios::in | std::ios::binary);
                if (!file_stream.is_open()) {
                    const Status status = Status::Error(
                        ErrorCode::kIoError,
                        "Cannot open file for reading: " + relative_path_str);
                    WriteJson(&response, 500, ErrorPayload(status));
                    return;
                }
                std::string file_content(
                    (std::istreambuf_iterator<char>(file_stream)),
                    std::istreambuf_iterator<char>());

                response.status = 200;
                response.set_header("Cache-Control", "no-store");
                response.set_header("Content-Disposition",
                                    "attachment; filename=\"" +
                                        target_file.filename().string() + "\"");
                response.set_content(file_content,
                                     "application/octet-stream");
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
                         runner_cache_.Clear();
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
                           runner_cache_.Invalidate(key_result.value());
                           WriteJson(&response, 200, EntrySummaryPayload(delete_result.value()));
                       });

        // 中文注释：POST /algorithms/{id}/{version}/{backend}/deployments
        // Body: DeploymentSpec JSON
        // 向指定算法追加一条部署记录。
        server_.Post(R"(/algorithms/([^/]+)/([^/]+)/([^/]+)/deployments)",
                     [this](const httplib::Request& request, httplib::Response& response) {
                         auto key_result = ParseKeyFromMatches(request);
                         if (!key_result.ok()) {
                             WriteJson(&response, HttpStatusForStatus(key_result.status()),
                                       ErrorPayload(key_result.status()));
                             return;
                         }

                         auto body_result = ParseJsonBody(request);
                         if (!body_result.ok()) {
                             WriteJson(&response, HttpStatusForStatus(body_result.status()),
                                       ErrorPayload(body_result.status()));
                             return;
                         }

                         auto spec_result = DeploymentSpecFromJson(body_result.value());
                         if (!spec_result.ok()) {
                             WriteJson(&response, HttpStatusForStatus(spec_result.status()),
                                       ErrorPayload(spec_result.status()));
                             return;
                         }

                         std::lock_guard<std::mutex> lock(mutex_);
                         const Status reload_status = ReloadRegistryLocked();
                         if (!reload_status.ok()) {
                             WriteJson(&response, HttpStatusForStatus(reload_status),
                                       ErrorPayload(reload_status));
                             return;
                         }

                         auto result =
                             registry_.AddDeployment(key_result.value(), spec_result.value());
                         if (!result.ok()) {
                             WriteJson(&response, HttpStatusForStatus(result.status()),
                                       ErrorPayload(result.status()));
                             return;
                         }
                         WriteJson(&response, 201, EntryPayload(result.value()));
                     });

        // 中文注释：DELETE /algorithms/{id}/{version}/{backend}/deployments/{deploy_id}
        // 从指定算法删除一条部署记录。
        server_.Delete(
            R"(/algorithms/([^/]+)/([^/]+)/([^/]+)/deployments/([^/]+))",
            [this](const httplib::Request& request, httplib::Response& response) {
                if (request.matches.size() < 5) {
                    const Status status =
                        InvalidArgument("URL must contain algorithm_id, version, "
                                        "backend_type, and deploy_id.");
                    WriteJson(&response, HttpStatusForStatus(status), ErrorPayload(status));
                    return;
                }
                auto key_result = ParseKeyFromMatches(request);
                if (!key_result.ok()) {
                    WriteJson(&response, HttpStatusForStatus(key_result.status()),
                              ErrorPayload(key_result.status()));
                    return;
                }
                const std::string deploy_id = request.matches[4].str();

                std::lock_guard<std::mutex> lock(mutex_);
                const Status reload_status = ReloadRegistryLocked();
                if (!reload_status.ok()) {
                    WriteJson(&response, HttpStatusForStatus(reload_status),
                              ErrorPayload(reload_status));
                    return;
                }

                auto result = registry_.RemoveDeployment(key_result.value(), deploy_id);
                if (!result.ok()) {
                    WriteJson(&response, HttpStatusForStatus(result.status()),
                              ErrorPayload(result.status()));
                    return;
                }
                WriteJson(&response, 200, EntryPayload(result.value()));
            });

        // 中文注释：PATCH /algorithms/{id}/{version}/{backend}/deployments/{deploy_id}/status
        // Body: { "deploy_status": "ready"|"loading"|"error"|"unloaded",
        //         "status_message": "...",
        //         "updated_at": "2024-01-01T00:00:00Z" }
        // 更新指定部署记录的状态。
        server_.Patch(
            R"(/algorithms/([^/]+)/([^/]+)/([^/]+)/deployments/([^/]+)/status)",
            [this](const httplib::Request& request, httplib::Response& response) {
                if (request.matches.size() < 5) {
                    const Status status =
                        InvalidArgument("URL must contain algorithm_id, version, "
                                        "backend_type, and deploy_id.");
                    WriteJson(&response, HttpStatusForStatus(status), ErrorPayload(status));
                    return;
                }
                auto key_result = ParseKeyFromMatches(request);
                if (!key_result.ok()) {
                    WriteJson(&response, HttpStatusForStatus(key_result.status()),
                              ErrorPayload(key_result.status()));
                    return;
                }
                const std::string deploy_id = request.matches[4].str();

                auto body_result = ParseJsonBody(request);
                if (!body_result.ok()) {
                    WriteJson(&response, HttpStatusForStatus(body_result.status()),
                              ErrorPayload(body_result.status()));
                    return;
                }

                const json& body = body_result.value();
                const std::string raw_status = body.value("deploy_status", std::string());
                if (raw_status.empty()) {
                    const Status status = InvalidArgument("Body must contain deploy_status.");
                    WriteJson(&response, HttpStatusForStatus(status), ErrorPayload(status));
                    return;
                }
                auto new_status_result = ParseDeploymentStatus(raw_status);
                if (!new_status_result.ok()) {
                    WriteJson(&response, HttpStatusForStatus(new_status_result.status()),
                              ErrorPayload(new_status_result.status()));
                    return;
                }

                const std::string status_message = body.value("status_message", std::string());
                const std::string updated_at = body.value("updated_at", std::string());

                std::lock_guard<std::mutex> lock(mutex_);
                const Status reload_status = ReloadRegistryLocked();
                if (!reload_status.ok()) {
                    WriteJson(&response, HttpStatusForStatus(reload_status),
                              ErrorPayload(reload_status));
                    return;
                }

                auto result = registry_.UpdateDeploymentStatus(
                    key_result.value(), deploy_id, new_status_result.value(),
                    status_message, updated_at);
                if (!result.ok()) {
                    WriteJson(&response, HttpStatusForStatus(result.status()),
                              ErrorPayload(result.status()));
                    return;
                }
                WriteJson(&response, 200, EntryPayload(result.value()));
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

            // 中文注释：registry 访问和 runner 分派均在 mutex 保护下进行，保证线程安全。
            // PythonHttpRunnerPool 内部的 Checkout/Return 使用自己的独立锁，不会与此 mutex 嵌套。
            // 注意：持锁期间 coordinator.Run() 可能阻塞（Python 网络 I/O），属于预期行为；
            // 真正的并发推理扩展点在 Runner 层（连接池），而非 Server 层的 mutex 粒度。
            std::lock_guard<std::mutex> lock(mutex_);
            const Status reload_status = ReloadRegistryLocked();
            if (!reload_status.ok()) {
                WriteJson(&response, HttpStatusForStatus(reload_status),
                          ErrorPayload(reload_status));
                return;
            }

            ExecutionCoordinator coordinator(registry_, config_.execution_log_path, &runner_cache_);
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

        // 中文注释：POST /load — 显式预加载模型到内存/显存（预热 runner 缓存）。
        // Body 字段（必填）：algorithm_id, version, backend_type
        // Body 字段（可选）：
        //   deploy_id        — 与注册表中 DeploymentSpec 对应；加载后自动更新 deploy_status
        //   transfer_mode    — "none"（默认）或 "http_pull"
        //   source_base_url  — http_pull 时，主库 HTTP Server 的 base URL
        //   target_local_dir — http_pull 时，节点本地存放目录（空则自动生成）
        //   files_to_pull    — http_pull 时，需下载的文件相对路径数组（空则自动推导）
        // load_status 取值："loaded" / "already_loaded" / "error"
        server_.Post("/load", [this](const httplib::Request& request,
                                     httplib::Response& response) {
            auto body_result = ParseJsonBody(request);
            if (!body_result.ok()) {
                WriteJson(&response, HttpStatusForStatus(body_result.status()),
                          ErrorPayload(body_result.status()));
                return;
            }
            const json& body = body_result.value();

            const std::string algorithm_id    = body.value("algorithm_id",    std::string());
            const std::string version         = body.value("version",         std::string());
            const std::string raw_backend     = body.value("backend_type",    std::string());
            const std::string deploy_id       = body.value("deploy_id",       std::string());
            const std::string transfer_mode   = body.value("transfer_mode",   std::string("none"));
            const std::string source_base_url = body.value("source_base_url", std::string());
            const std::string target_local_dir= body.value("target_local_dir",std::string());
            // 中文注释：Python 并发连接池容量（0 = 使用 RuntimeFactory 默认值 4）
            const int pool_size_raw           = body.value("pool_size",       0);
            const int pool_checkout_timeout   = body.value("pool_checkout_timeout_ms", 0);
            const std::size_t pool_size =
                pool_size_raw > 0 ? static_cast<std::size_t>(pool_size_raw) : 0;

            if (algorithm_id.empty() || version.empty() || raw_backend.empty()) {
                const Status s = InvalidArgument(
                    "Body must contain algorithm_id, version, and backend_type.");
                WriteJson(&response, 400, ErrorPayload(s));
                return;
            }
            auto backend_result = ParseBackendType(raw_backend);
            if (!backend_result.ok()) {
                WriteJson(&response, HttpStatusForStatus(backend_result.status()),
                          ErrorPayload(backend_result.status()));
                return;
            }

            // 中文注释：解析 files_to_pull 数组（可选）
            std::vector<std::string> files_to_pull;
            if (body.contains("files_to_pull") && body.at("files_to_pull").is_array()) {
                for (const auto& f : body.at("files_to_pull")) {
                    if (f.is_string()) {
                        files_to_pull.push_back(f.get<std::string>());
                    }
                }
            }

            std::lock_guard<std::mutex> lock(mutex_);
            const Status reload_status = ReloadRegistryLocked();
            if (!reload_status.ok()) {
                WriteJson(&response, HttpStatusForStatus(reload_status),
                          ErrorPayload(reload_status));
                return;
            }

            // 中文注释：默认 factory 使用全局默认 pool_size；
            // 若请求中指定了 pool_size，ModelLoader 内部会临时覆盖 factory 参数。
            const RuntimeFactory factory;
            ModelLoader loader(registry_, runner_cache_, factory);
            ModelLoadRequest load_req;
            load_req.algorithm_id         = algorithm_id;
            load_req.version              = version;
            load_req.backend_type         = backend_result.value();
            load_req.deploy_id            = deploy_id;
            load_req.transfer_mode        = transfer_mode;
            load_req.source_base_url      = source_base_url;
            load_req.target_local_dir     = target_local_dir;
            load_req.files_to_pull        = std::move(files_to_pull);
            load_req.pool_size            = pool_size;
            load_req.pool_checkout_timeout_ms = pool_checkout_timeout;
            const ModelLoadResult load_result = loader.Load(load_req);
            const int status_code = load_result.ok ? 200 : 502;
            WriteJson(&response, status_code, ToJson(load_result));
        });

        // 中文注释：POST /unload — 显式卸载模型，释放 runner 缓存及显存/内存。
        // Body: { "algorithm_id": "...", "version": "...", "backend_type": "...",
        //         "deploy_id": "<可选>" }
        // load_status 取值："unloaded"
        server_.Post("/unload", [this](const httplib::Request& request,
                                       httplib::Response& response) {
            auto body_result = ParseJsonBody(request);
            if (!body_result.ok()) {
                WriteJson(&response, HttpStatusForStatus(body_result.status()),
                          ErrorPayload(body_result.status()));
                return;
            }
            const json& body = body_result.value();

            const std::string algorithm_id = body.value("algorithm_id", std::string());
            const std::string version      = body.value("version", std::string());
            const std::string raw_backend  = body.value("backend_type", std::string());
            const std::string deploy_id    = body.value("deploy_id",    std::string());

            if (algorithm_id.empty() || version.empty() || raw_backend.empty()) {
                const Status s = InvalidArgument(
                    "Body must contain algorithm_id, version, and backend_type.");
                WriteJson(&response, 400, ErrorPayload(s));
                return;
            }
            auto backend_result = ParseBackendType(raw_backend);
            if (!backend_result.ok()) {
                WriteJson(&response, HttpStatusForStatus(backend_result.status()),
                          ErrorPayload(backend_result.status()));
                return;
            }

            std::lock_guard<std::mutex> lock(mutex_);
            const Status reload_status = ReloadRegistryLocked();
            if (!reload_status.ok()) {
                WriteJson(&response, HttpStatusForStatus(reload_status),
                          ErrorPayload(reload_status));
                return;
            }

            const RuntimeFactory factory;
            ModelLoader loader(registry_, runner_cache_, factory);
            const ModelLoadRequest unload_req{
                algorithm_id, version, backend_result.value(), deploy_id};
            const ModelLoadResult unload_result = loader.Unload(unload_req);
            WriteJson(&response, 200, ToJson(unload_result));
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
            runner_cache_.Invalidate(key_result.value());
            WriteJson(&response, 200, EntrySummaryPayload(result.value()));
        });
    }

    HttpServerConfig config_;
    AlgorithmRegistry registry_;
    RuntimeRunnerCache runner_cache_;
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
