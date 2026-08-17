#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/io/file_utils.h"
#include "algolib/io/json_utils.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/execution_coordinator.h"
#include "algolib/runtime/model_loader.h"
#include "algolib/runtime/runtime_factory.h"
#include "algolib/runtime/runtime_runner_cache.h"

namespace {

using algolib::AlgorithmEntry;
using algolib::AlgorithmKey;
using algolib::AlgorithmQueryFilter;
using algolib::AlgorithmRegistry;
using algolib::AlgorithmRequestFromJson;
using algolib::ExecutionCoordinator;
using algolib::FileUtils;
using algolib::JsonUtils;
using algolib::ModelLoadRequest;
using algolib::ModelLoader;
using algolib::ParseAlgorithmStatus;
using algolib::ParseBackendType;
using algolib::RuntimeFactory;
using algolib::RuntimeRunnerCache;
using algolib::Status;

std::filesystem::path ResolveRegistryPath() {
    if (const char* env_value = std::getenv("ALGOLIB_REGISTRY_PATH"); env_value != nullptr) {
        return std::filesystem::path(env_value);
    }
    return std::filesystem::current_path() / ".algolib" / "registry.json";
}

void PrintUsage() {
    std::cout
        << "algolib register <package_or_card_path>\n"
        << "algolib validate <algorithm_id> <version> <backend_type>\n"
        << "algolib activate <algorithm_id> <version> <backend_type>\n"
        << "algolib disable <algorithm_id> <version> <backend_type>\n"
        << "algolib delete <algorithm_id> <version> <backend_type>\n"
        << "algolib load <algorithm_id> <version> <backend_type> [<deploy_id>]\n"
        << "algolib unload <algorithm_id> <version> <backend_type> [<deploy_id>]\n"
        << "algolib list [OPTIONS]\n"
        << "  --active-only=<true|false>    默认 true；false 时返回所有非 deleted 条目\n"
        << "  --status=<draft|validated|active|disabled>\n"
        << "                               仅 active-only=false 时生效\n"
        << "  --task-family=<string>        精确匹配 task_family 字段\n"
        << "  --backend=<onnx|python_http_service>\n"
        << "  --capability=<string>        capabilities 列表中包含该值\n"
        << "  --node=<node_id>             deployments 中至少有一个 node_id 等于该值\n"
        << "  --max-vram=<MB>              筛选 min_vram_mb <= max-vram 的模型\n"
        << "  --max-memory=<MB>            筛选 min_memory_mb <= max-memory 的模型\n"
        << "  --max-cpu-cores=<n>          筛选 min_cpu_cores <= max-cpu-cores 的模型\n"
        << "algolib show-card <algorithm_id> <version> <backend_type>\n"
        << "algolib run <request_json_path>\n"
        << "algolib deploy <algorithm_id> <version> <backend_type> <deploy_spec_json_path>\n"
        << "algolib undeploy <algorithm_id> <version> <backend_type> <deploy_id>\n"
        << "algolib deploy-status <algorithm_id> <version> <backend_type> <deploy_id>"
           " <loading|ready|error|unloaded> [status_message] [updated_at]\n";
}

nlohmann::json BuildEntrySummary(const AlgorithmEntry& entry) {
    return {
        {"algorithm_id", entry.key.algorithm_id},
        {"version", entry.key.version},
        {"backend_type", algolib::ToString(entry.key.backend_type)},
        {"status", algolib::ToString(entry.status)},
        {"display_name", entry.card.display_name},
        {"task_family", entry.card.task_family},
    };
}

AlgorithmKey ParseKeyOrThrow(const std::vector<std::string>& args, std::size_t start_index) {
    if (args.size() <= start_index + 2) {
        throw std::runtime_error("Expected <algorithm_id> <version> <backend_type>.");
    }

    auto backend_result = ParseBackendType(args[start_index + 2]);
    if (!backend_result.ok()) {
        throw std::runtime_error(backend_result.status().ToString());
    }

    return AlgorithmKey{
        args[start_index],
        args[start_index + 1],
        backend_result.value(),
    };
}

int PrintStatusError(const Status& status) {
    nlohmann::json error_json{
        {"ok", false},
        {"error_code", algolib::ToString(status.code())},
        {"message", status.message()},
    };
    std::cerr << JsonUtils::Pretty(error_json) << std::endl;
    return 1;
}

void PrintJson(const nlohmann::json& payload) {
    std::cout << JsonUtils::Pretty(payload) << std::endl;
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        std::vector<std::string> args(argv + 1, argv + argc);
        if (args.empty()) {
            PrintUsage();
            return 1;
        }

        AlgorithmRegistry registry(ResolveRegistryPath());
        auto reload_status = registry.Reload();
        if (!reload_status.ok()) {
            return PrintStatusError(reload_status);
        }

        const std::string& command = args[0];

        if (command == "register") {
            if (args.size() != 2) {
                PrintUsage();
                return 1;
            }
            auto result = registry.Register(args[1]);
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"algorithm_id", result.value().key.algorithm_id},
                {"version", result.value().key.version},
                {"backend_type", algolib::ToString(result.value().key.backend_type)},
                {"status", algolib::ToString(result.value().status)},
            });
            return 0;
        }

        if (command == "validate") {
            AlgorithmKey key = ParseKeyOrThrow(args, 1);
            auto result = registry.Validate(key);
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"algorithm_id", result.value().key.algorithm_id},
                {"version", result.value().key.version},
                {"backend_type", algolib::ToString(result.value().key.backend_type)},
                {"status", algolib::ToString(result.value().status)},
            });
            return 0;
        }

        if (command == "activate") {
            AlgorithmKey key = ParseKeyOrThrow(args, 1);
            auto result = registry.Activate(key);
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"algorithm_id", result.value().key.algorithm_id},
                {"version", result.value().key.version},
                {"backend_type", algolib::ToString(result.value().key.backend_type)},
                {"status", algolib::ToString(result.value().status)},
            });
            return 0;
        }

        if (command == "disable") {
            AlgorithmKey key = ParseKeyOrThrow(args, 1);
            auto result = registry.Disable(key);
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"algorithm_id", result.value().key.algorithm_id},
                {"version", result.value().key.version},
                {"backend_type", algolib::ToString(result.value().key.backend_type)},
                {"status", algolib::ToString(result.value().status)},
            });
            return 0;
        }

        if (command == "delete") {
            AlgorithmKey key = ParseKeyOrThrow(args, 1);
            auto result = registry.Delete(key);
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"algorithm_id", result.value().key.algorithm_id},
                {"version", result.value().key.version},
                {"backend_type", algolib::ToString(result.value().key.backend_type)},
                {"status", algolib::ToString(result.value().status)},
            });
            return 0;
        }

        // 中文注释：load — 显式预加载模型到 runner 缓存。
        // 用法：algolib load <id> <version> <backend> [<deploy_id>]
        // deploy_id 可选；若指定则加载完成后自动将部署状态改为 ready。
        if (command == "load") {
            if (args.size() < 4 || args.size() > 5) {
                PrintUsage();
                return 1;
            }
            algolib::AlgorithmKey key = ParseKeyOrThrow(args, 1);
            const std::string deploy_id = args.size() == 5 ? args[4] : std::string();

            RuntimeRunnerCache runner_cache;
            const RuntimeFactory factory;
            ModelLoader loader(registry, runner_cache, factory);
            const ModelLoadRequest load_req{key.algorithm_id, key.version,
                                            key.backend_type, deploy_id};
            const auto load_result = loader.Load(load_req);
            PrintJson(algolib::ToJson(load_result));
            return load_result.ok ? 0 : 1;
        }

        // 中文注释：unload — 从 runner 缓存中移除模型，释放资源。
        // 用法：algolib unload <id> <version> <backend> [<deploy_id>]
        if (command == "unload") {
            if (args.size() < 4 || args.size() > 5) {
                PrintUsage();
                return 1;
            }
            algolib::AlgorithmKey key = ParseKeyOrThrow(args, 1);
            const std::string deploy_id = args.size() == 5 ? args[4] : std::string();

            RuntimeRunnerCache runner_cache;
            const RuntimeFactory factory;
            ModelLoader loader(registry, runner_cache, factory);
            const ModelLoadRequest unload_req{key.algorithm_id, key.version,
                                              key.backend_type, deploy_id};
            const auto unload_result = loader.Unload(unload_req);
            PrintJson(algolib::ToJson(unload_result));
            return 0;
        }

        // 中文注释：list — 多维过滤查询，支持 --key=value 风格选项。
        // 无位置参数（除 command 本身）；所有选项均可选，不传则使用默认值。
        if (command == "list") {
            // 解析 --key=value 格式的选项
            AlgorithmQueryFilter filter;
            filter.active_only = true;  // 默认只返回 active

            for (std::size_t i = 1; i < args.size(); ++i) {
                const std::string& arg = args[i];
                // 辅助 lambda：提取 "--key=value" 中的 value 部分
                auto ExtractValue = [&arg](const std::string& prefix) -> std::string {
                    if (arg.size() > prefix.size() && arg.substr(0, prefix.size()) == prefix) {
                        return arg.substr(prefix.size());
                    }
                    return {};
                };

                std::string v;

                // --active-only=<true|false>
                v = ExtractValue("--active-only=");
                if (!v.empty()) {
                    filter.active_only = !(v == "false" || v == "0" || v == "no");
                    continue;
                }

                // --status=<draft|validated|active|disabled>
                v = ExtractValue("--status=");
                if (!v.empty()) {
                    auto parsed = ParseAlgorithmStatus(v);
                    if (parsed.ok()) {
                        filter.status = parsed.value();
                    } else {
                        std::cerr << "Unknown status value: " << v << "\n";
                        return 1;
                    }
                    continue;
                }

                // --task-family=<string>
                v = ExtractValue("--task-family=");
                if (!v.empty()) {
                    filter.task_family = v;
                    continue;
                }

                // --backend=<onnx|python_http_service>
                v = ExtractValue("--backend=");
                if (!v.empty()) {
                    auto parsed = ParseBackendType(v);
                    if (parsed.ok()) {
                        filter.backend_type = parsed.value();
                    } else {
                        std::cerr << "Unknown backend value: " << v << "\n";
                        return 1;
                    }
                    continue;
                }

                // --capability=<string>
                v = ExtractValue("--capability=");
                if (!v.empty()) {
                    filter.capability = v;
                    continue;
                }

                // --node=<node_id>
                v = ExtractValue("--node=");
                if (!v.empty()) {
                    filter.node_id = v;
                    continue;
                }

                // --max-vram=<MB>
                v = ExtractValue("--max-vram=");
                if (!v.empty()) {
                    try {
                        filter.max_vram_mb = std::stoi(v);
                    } catch (...) {
                        std::cerr << "Invalid value for --max-vram: " << v << "\n";
                        return 1;
                    }
                    continue;
                }

                // --max-memory=<MB>
                v = ExtractValue("--max-memory=");
                if (!v.empty()) {
                    try {
                        filter.max_memory_mb = std::stoi(v);
                    } catch (...) {
                        std::cerr << "Invalid value for --max-memory: " << v << "\n";
                        return 1;
                    }
                    continue;
                }

                // --max-cpu-cores=<n>
                v = ExtractValue("--max-cpu-cores=");
                if (!v.empty()) {
                    try {
                        filter.max_cpu_cores = std::stoi(v);
                    } catch (...) {
                        std::cerr << "Invalid value for --max-cpu-cores: " << v << "\n";
                        return 1;
                    }
                    continue;
                }

                // 未知选项
                std::cerr << "Unknown option: " << arg << "\n";
                PrintUsage();
                return 1;
            }

            nlohmann::json list_json = nlohmann::json::array();
            for (const auto& entry : registry.Query(filter)) {
                list_json.push_back(BuildEntrySummary(entry));
            }
            PrintJson(list_json);
            return 0;
        }

        if (command == "show-card") {
            AlgorithmKey key = ParseKeyOrThrow(args, 1);
            auto result = registry.Get(key);
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"entry", algolib::ToJson(result.value())},
                {"agent_view", algolib::ToAgentViewJson(result.value())},
            });
            return 0;
        }

        if (command == "run") {
            if (args.size() != 2) {
                PrintUsage();
                return 1;
            }

            auto request_path_result = FileUtils::NormalizeInputPath(args[1]);
            if (!request_path_result.ok()) {
                return PrintStatusError(request_path_result.status());
            }

            auto request_json = JsonUtils::ReadJsonFile(request_path_result.value());
            if (!request_json.ok()) {
                return PrintStatusError(request_json.status());
            }

            auto request_result = AlgorithmRequestFromJson(request_json.value());
            if (!request_result.ok()) {
                return PrintStatusError(request_result.status());
            }

            ExecutionCoordinator coordinator(registry);
            const auto run_result = coordinator.Run(request_result.value());
            PrintJson(algolib::ToJson(run_result));
            return run_result.ok ? 0 : 1;
        }

        // 中文注释：deploy — 从 JSON 文件读取 DeploymentSpec 并追加到指定算法的部署列表。
        // 用法：algolib deploy <id> <version> <backend> <spec.json>
        if (command == "deploy") {
            if (args.size() != 5) {
                PrintUsage();
                return 1;
            }
            algolib::AlgorithmKey key = ParseKeyOrThrow(args, 1);

            auto spec_path_result = FileUtils::NormalizeInputPath(args[4]);
            if (!spec_path_result.ok()) {
                return PrintStatusError(spec_path_result.status());
            }
            auto spec_json_result = JsonUtils::ReadJsonFile(spec_path_result.value());
            if (!spec_json_result.ok()) {
                return PrintStatusError(spec_json_result.status());
            }
            auto spec_result = algolib::DeploymentSpecFromJson(spec_json_result.value());
            if (!spec_result.ok()) {
                return PrintStatusError(spec_result.status());
            }

            auto result = registry.AddDeployment(key, spec_result.value());
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"algorithm_id", result.value().key.algorithm_id},
                {"version", result.value().key.version},
                {"backend_type", algolib::ToString(result.value().key.backend_type)},
                {"deploy_id", spec_result.value().deploy_id},
                {"deploy_status", algolib::ToString(spec_result.value().deploy_status)},
            });
            return 0;
        }

        // 中文注释：undeploy — 从指定算法的部署列表中删除一条部署记录。
        // 用法：algolib undeploy <id> <version> <backend> <deploy_id>
        if (command == "undeploy") {
            if (args.size() != 5) {
                PrintUsage();
                return 1;
            }
            algolib::AlgorithmKey key = ParseKeyOrThrow(args, 1);
            const std::string deploy_id = args[4];

            auto result = registry.RemoveDeployment(key, deploy_id);
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"algorithm_id", result.value().key.algorithm_id},
                {"version", result.value().key.version},
                {"backend_type", algolib::ToString(result.value().key.backend_type)},
                {"removed_deploy_id", deploy_id},
                {"remaining_deployments",
                 static_cast<int>(result.value().deployments.size())},
            });
            return 0;
        }

        // 中文注释：deploy-status — 更新指定部署记录的状态。
        // 用法：algolib deploy-status <id> <version> <backend> <deploy_id>
        //       <loading|ready|error|unloaded> [status_message] [updated_at]
        if (command == "deploy-status") {
            if (args.size() < 6) {
                PrintUsage();
                return 1;
            }
            algolib::AlgorithmKey key = ParseKeyOrThrow(args, 1);
            const std::string deploy_id = args[4];
            const std::string raw_status = args[5];
            const std::string status_message = args.size() >= 7 ? args[6] : std::string();
            const std::string updated_at = args.size() >= 8 ? args[7] : std::string();

            auto new_status_result = algolib::ParseDeploymentStatus(raw_status);
            if (!new_status_result.ok()) {
                return PrintStatusError(new_status_result.status());
            }

            auto result = registry.UpdateDeploymentStatus(
                key, deploy_id, new_status_result.value(), status_message, updated_at);
            if (!result.ok()) {
                return PrintStatusError(result.status());
            }
            PrintJson({
                {"ok", true},
                {"algorithm_id", result.value().key.algorithm_id},
                {"version", result.value().key.version},
                {"backend_type", algolib::ToString(result.value().key.backend_type)},
                {"deploy_id", deploy_id},
                {"deploy_status", algolib::ToString(new_status_result.value())},
                {"status_message", status_message},
                {"updated_at", updated_at},
            });
            return 0;
        }

        PrintUsage();
        return 1;
    } catch (const std::exception& ex) {
        nlohmann::json error_json{
            {"ok", false},
            {"error_code", "INVALID_ARGUMENT"},
            {"message", ex.what()},
        };
        std::cerr << error_json.dump(2) << std::endl;
        return 1;
    }
}
