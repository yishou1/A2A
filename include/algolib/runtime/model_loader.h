#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/runtime/runtime_factory.h"
#include "algolib/runtime/runtime_runner_cache.h"

namespace algolib {

// 中文注释：ModelLoadRequest — 模型加载/卸载请求体。
// algorithm_id + version + backend_type 唯一定位注册表条目；
// deploy_id 可选：若非空，则加载完成后将对应部署实例的状态更新为 ready（卸载则更新为 unloaded）。
struct ModelLoadRequest {
    std::string algorithm_id;
    std::string version;
    BackendType backend_type = BackendType::kOnnx;

    // 可选：与 DeploymentSpec 中的 deploy_id 对应，用于分布式场景的状态回写。
    std::string deploy_id;

    // 中文注释：文件传输模式。
    // "none"       — 文件已在目标节点本地，直接加载（默认）。
    // "http_pull"  — 从 source_base_url 提供的主库下载整个 package。
    std::string transfer_mode = "none";

    // http_pull 模式：主库 HTTP Server 的 base URL，例如 "http://192.168.1.10:8088"
    std::string source_base_url;

    // http_pull 模式：文件在目标节点上的存放目录。
    // 空则自动生成（默认使用 /tmp/algolib_models/{algorithm_id}/{version}）。
    std::string target_local_dir;

    // http_pull 模式：需要拉取的相对文件路径列表（相对于 package_root）。
    // 若为空，则自动从注册表条目中推导所需文件集合。
    std::vector<std::string> files_to_pull;

    // 中文注释：Python HTTP Service 并发连接池容量。
    // 仅对 backend_type=python_http_service 有效；0 表示使用 RuntimeFactory 默认值。
    // 建议设置为 Python 服务进程的 worker 线程数（通常 4~16）。
    std::size_t pool_size = 0;

    // 中文注释：连接池 Checkout 超时（毫秒）。0 表示使用默认值（5000ms）。
    int pool_checkout_timeout_ms = 0;
};

// 中文注释：ModelLoadResult — 加载/卸载操作的返回结果。
struct ModelLoadResult {
    bool        ok           = false;
    std::string algorithm_id;
    std::string version;
    std::string backend_type;
    std::string deploy_id;
    // "loaded" / "unloaded" / "already_loaded" / "not_found"
    std::string load_status;
    std::string message;
    // 健康检查结果（仅 Load 成功时填充）
    bool        health_ok    = false;
    std::string health_status;
    std::string health_message;
};

// 中文注释：ModelLoader — 将"显式预加载/卸载"操作封装为单一职责类。
// 内部依赖 RuntimeRunnerCache 实现 runner 的缓存与复用；
// 加载完成后可选地通过 AlgorithmRegistry 将对应 DeploymentSpec 的状态更新为 ready/unloaded，
// 从而让 agent_view 中的 ready_endpoints 自动反映最新状态。
class ModelLoader {
public:
    // 中文注释：registry 和 runner_cache 的生命周期须长于 ModelLoader。
    ModelLoader(AlgorithmRegistry& registry,
                RuntimeRunnerCache& runner_cache,
                const RuntimeFactory& factory);

    // 中文注释：Load — 将模型预热进 runner 缓存。
    // 若已在缓存中则跳过加载并返回 load_status="already_loaded"。
    // 若指定了 deploy_id，加载成功后自动将该部署实例状态改为 DeploymentStatus::kReady。
    ModelLoadResult Load(const ModelLoadRequest& request);

    // 中文注释：Unload — 将 runner 从缓存中移除并调用 runner->Unload() 释放资源。
    // 若指定了 deploy_id，卸载后自动将该部署实例状态改为 DeploymentStatus::kUnloaded。
    ModelLoadResult Unload(const ModelLoadRequest& request);

private:
    AlgorithmRegistry&    registry_;
    RuntimeRunnerCache&   runner_cache_;
    const RuntimeFactory& factory_;
};

nlohmann::json ToJson(const ModelLoadResult& result);

}  // namespace algolib
