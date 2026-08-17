#include "algolib/runtime/model_loader.h"

#include <chrono>
#include <filesystem>
#include <string>
#include <vector>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/algorithm_status.h"
#include "algolib/core/backend_type.h"
#include "algolib/core/error_code.h"
#include "algolib/io/http_client.h"

namespace algolib {
namespace {

// 中文注释：BuildLoadResult — 构造失败的 ModelLoadResult（避免在每个返回点重复）。
ModelLoadResult BuildFailResult(const ModelLoadRequest& req,
                                const std::string& message,
                                ErrorCode code = ErrorCode::kInvalidArgument) {
    (void)code;
    ModelLoadResult r;
    r.ok           = false;
    r.algorithm_id = req.algorithm_id;
    r.version      = req.version;
    r.backend_type = ToString(req.backend_type);
    r.deploy_id    = req.deploy_id;
    r.load_status  = "error";
    r.message      = message;
    return r;
}

// 中文注释：GetCurrentTimestamp — 返回 ISO 8601 格式的当前 UTC 时间字符串。
std::string GetCurrentTimestamp() {
    const auto now =
        std::chrono::system_clock::now();
    const auto now_t = std::chrono::system_clock::to_time_t(now);
    char buf[32] = {};
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&now_t));
    return buf;
}

// 中文注释：InferFilesToPull — 从 AlgorithmCard 中自动推导需要传输的文件清单。
// 包含：model.onnx、preprocess.json、postprocess.json、label_map、tensor_contract、tokenizer、
//         input_schema.json、output_schema.json。
// 所有路径均为相对于 package_root 的相对路径。
std::vector<std::string> InferFilesToPull(const AlgorithmCard& card) {
    std::vector<std::string> files;
    auto add = [&](const std::string& path) {
        if (!path.empty()) {
            files.push_back(path);
        }
    };
    add(card.machine_spec.runtime.model_uri);
    add(card.machine_spec.input_schema_ref);
    add(card.machine_spec.output_schema_ref);
    add(card.machine_spec.tensor_contract_ref);
    if (card.machine_spec.preprocess.has_value()) {
        add(card.machine_spec.preprocess->config_uri);
        add(card.machine_spec.preprocess->label_map_uri);
    }
    if (card.machine_spec.postprocess.has_value()) {
        add(card.machine_spec.postprocess->config_uri);
        add(card.machine_spec.postprocess->label_map_uri);
    }
    if (card.machine_spec.tokenizer.has_value()) {
        add(card.machine_spec.tokenizer->tokenizer_uri);
    }
    return files;
}

// 中文注释：PullFilesViaHttp — 通过主库的 /files 端点下载模型文件包到目标节点本地。
// base_url 示例："http://192.168.1.10:8088"
// 返回目标节点上的本地目录，后续 OnnxRunner::Load 从该目录读取文件。
Status PullFilesViaHttp(const std::string& base_url,
                        const std::string& algorithm_id,
                        const std::string& version,
                        const std::string& backend_str,
                        const std::vector<std::string>& files,
                        const std::filesystem::path& target_dir) {
    HttpClient http_client;
    for (const auto& rel_path : files) {
        if (rel_path.empty()) {
            continue;
        }
        // 构造下载 URL：{base_url}/algorithms/{id}/{version}/{backend}/files/{rel_path}
        const std::string url = base_url + "/algorithms/" + algorithm_id + "/" + version +
                                "/" + backend_str + "/files/" + rel_path;
        const std::filesystem::path dest = target_dir / rel_path;

        auto status = http_client.DownloadFile(url, dest, /*timeout_ms=*/120000);
        if (!status.ok()) {
            return Status::Error(
                ErrorCode::kIoError,
                "Failed to download " + rel_path + " from " + url + ": " +
                    status.message());
        }
    }
    return Status::Ok();
}

}  // namespace

// ---------------------------------------------------------------------------
// ModelLoader 构造
// ---------------------------------------------------------------------------

ModelLoader::ModelLoader(AlgorithmRegistry& registry,
                         RuntimeRunnerCache& runner_cache,
                         const RuntimeFactory& factory)
    : registry_(registry), runner_cache_(runner_cache), factory_(factory) {}

// ---------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------

ModelLoadResult ModelLoader::Load(const ModelLoadRequest& request) {
    // 1. 从注册表获取条目
    const AlgorithmKey key{request.algorithm_id, request.version, request.backend_type};
    auto entry_result = registry_.Get(key);
    if (!entry_result.ok()) {
        return BuildFailResult(request, entry_result.status().message());
    }
    const AlgorithmEntry& entry = entry_result.value();

    // 2. 只允许加载处于 active 状态的算法
    if (entry.status != AlgorithmStatus::kActive) {
        return BuildFailResult(
            request,
            "Algorithm must be active to load; current status=" + ToString(entry.status) + ".");
    }

    // 3. 检查是否已加载（仅 ONNX 有缓存语义）
    const bool already = runner_cache_.IsLoaded(entry, request.deploy_id);

    ModelLoadResult result;
    result.algorithm_id = request.algorithm_id;
    result.version      = request.version;
    result.backend_type = ToString(request.backend_type);
    result.deploy_id    = request.deploy_id;

    if (already) {
        // 中文注释：已在缓存中，无需重新加载；直接做健康检查后返回。
        auto cached = runner_cache_.GetCachedRunner(entry, request.deploy_id);
        result.ok          = true;
        result.load_status = "already_loaded";
        result.message     = "Runner is already loaded in cache.";
        if (cached) {
            const HealthStatus hs = cached->HealthCheck();
            result.health_ok      = hs.ok;
            result.health_status  = hs.status;
            result.health_message = hs.message;
        }
    } else {
        // 中文注释：加载前先将状态改为 loading。
        if (!request.deploy_id.empty()) {
            registry_.UpdateDeploymentStatus(
                key, request.deploy_id,
                DeploymentStatus::kLoading,
                "Model loading started.",
                GetCurrentTimestamp());
        }

        // 中文注释：step A — 若为 http_pull 模式，先从主库下载文件包。
        AlgorithmEntry effective_entry = entry;  // 可能修改 package_root
        if (request.transfer_mode == "http_pull") {
            if (request.source_base_url.empty()) {
                if (!request.deploy_id.empty()) {
                    registry_.UpdateDeploymentStatus(
                        key, request.deploy_id, DeploymentStatus::kError,
                        "http_pull requires source_base_url.", GetCurrentTimestamp());
                }
                return BuildFailResult(request,
                    "transfer_mode=http_pull requires source_base_url.");
            }

            // 確定目标本地目录
            const std::filesystem::path target_dir =
                request.target_local_dir.empty()
                    ? std::filesystem::path("/tmp/algolib_models") /
                          request.algorithm_id / request.version
                    : std::filesystem::path(request.target_local_dir);

            // 确定需要下载的文件列表
            const std::vector<std::string> files =
                request.files_to_pull.empty()
                    ? InferFilesToPull(entry.card)
                    : request.files_to_pull;

            const std::string backend_str = ToString(request.backend_type);
            auto pull_status = PullFilesViaHttp(
                request.source_base_url,
                request.algorithm_id,
                request.version,
                backend_str,
                files,
                target_dir);

            if (!pull_status.ok()) {
                if (!request.deploy_id.empty()) {
                    registry_.UpdateDeploymentStatus(
                        key, request.deploy_id, DeploymentStatus::kError,
                        "File pull failed: " + pull_status.message(),
                        GetCurrentTimestamp());
                }
                return BuildFailResult(request, pull_status.message());
            }

            // 中文注释：更新 effective_entry.package_root 为本地下载目录，
            // 这样 OnnxRunner::Load 就会从该目录读取文件。
            effective_entry.package_root = target_dir;

            // 中文注释：将文件下载位置持久化到注册表（local_model_path 字段），
            // 并将状态更新为 loading（等待 ONNX session 初始化完成）。
            // 注意：这里通过 UpdateDeploymentStatus 的新增 local_model_path 参数
            // 一次性完成状态机转换 + 路径回写 + 持久化，确保节点重启后仍可查询到本地路径。
            if (!request.deploy_id.empty()) {
                const std::string local_model_path =
                    (target_dir / entry.card.machine_spec.runtime.model_uri)
                        .generic_string();
                registry_.UpdateDeploymentStatus(
                    key, request.deploy_id,
                    DeploymentStatus::kLoading,
                    "Files pulled to " + target_dir.generic_string() +
                        "; starting session load.",
                    GetCurrentTimestamp(),
                    local_model_path);  // ← 持久化到注册表
            }
        }

        // step B — 创建 runner 并加载（展入缓存），传入 deploy_id 以使用复合 cache key。
        // 中文注释：若请求指定了 pool_size，则临时构造一个覆盖了 pool_size 的 factory，
        // 以便 RuntimeFactory::Create 生成正确容量的 PythonHttpRunnerPool。
        const RuntimeFactory* effective_factory = &factory_;
        RuntimeFactory local_factory;
        if (request.pool_size > 0 &&
            request.backend_type == BackendType::kPythonHttpService) {
            local_factory = RuntimeFactory(
                request.pool_size,
                request.pool_checkout_timeout_ms > 0 ? request.pool_checkout_timeout_ms : 5000);
            effective_factory = &local_factory;
        }
        auto runner_result = runner_cache_.GetOrLoad(effective_entry, *effective_factory, request.deploy_id);
        if (!runner_result.ok()) {
            if (!request.deploy_id.empty()) {
                registry_.UpdateDeploymentStatus(
                    key, request.deploy_id,
                    DeploymentStatus::kError,
                    "Load failed: " + runner_result.status().message(),
                    GetCurrentTimestamp());
            }
            return BuildFailResult(request, runner_result.status().message());
        }

        // 加载成功
        result.ok          = true;
        result.load_status = "loaded";
        result.message     = request.transfer_mode == "http_pull"
                                 ? "Files pulled and model loaded successfully."
                                 : "Model loaded successfully.";

        const HealthStatus hs = runner_result.value()->HealthCheck();
        result.health_ok      = hs.ok;
        result.health_status  = hs.status;
        result.health_message = hs.message;

        // 部署状态回写 → ready
        if (!request.deploy_id.empty()) {
            registry_.UpdateDeploymentStatus(
                key, request.deploy_id,
                DeploymentStatus::kReady,
                "Model loaded and ready.",
                GetCurrentTimestamp());
        }
    }

    return result;
}

// ---------------------------------------------------------------------------
// Unload
// ---------------------------------------------------------------------------

ModelLoadResult ModelLoader::Unload(const ModelLoadRequest& request) {
    const AlgorithmKey key{request.algorithm_id, request.version, request.backend_type};
    auto entry_result = registry_.Get(key);
    if (!entry_result.ok()) {
        return BuildFailResult(request, entry_result.status().message());
    }
    const AlgorithmEntry& entry = entry_result.value();

    ModelLoadResult result;
    result.algorithm_id = request.algorithm_id;
    result.version      = request.version;
    result.backend_type = ToString(request.backend_type);
    result.deploy_id    = request.deploy_id;

    // 中文注释：尝试从缓存中拿到 runner 并调用 Unload()，随后从缓存删除指定 deploy_id 条目。
    auto cached = runner_cache_.GetCachedRunner(entry, request.deploy_id);
    if (cached) {
        cached->Unload();  // 忽略 Unload 返回值（资源释放尽力而为）
    }
    // 若指定了 deploy_id 则只清除该条目；否则清除所有同名算法缓存（全卸载语义）
    if (!request.deploy_id.empty()) {
        runner_cache_.InvalidateDeployment(key, request.deploy_id);
    } else {
        runner_cache_.Invalidate(key);
    }

    result.ok          = true;
    result.load_status = "unloaded";
    result.message     = cached ? "Runner unloaded from cache."
                                : "Runner was not in cache; no-op.";

    // 部署状态回写
    if (!request.deploy_id.empty()) {
        registry_.UpdateDeploymentStatus(
            key, request.deploy_id,
            DeploymentStatus::kUnloaded,
            "Model unloaded.",
            GetCurrentTimestamp());
    }

    return result;
}

// ---------------------------------------------------------------------------
// ToJson
// ---------------------------------------------------------------------------

nlohmann::json ToJson(const ModelLoadResult& result) {
    nlohmann::json j{
        {"ok",           result.ok},
        {"algorithm_id", result.algorithm_id},
        {"version",      result.version},
        {"backend_type", result.backend_type},
        {"load_status",  result.load_status},
        {"message",      result.message},
    };
    if (!result.deploy_id.empty()) {
        j["deploy_id"] = result.deploy_id;
    }
    if (result.ok && result.load_status != "unloaded") {
        j["health"] = {
            {"ok",      result.health_ok},
            {"status",  result.health_status},
            {"message", result.health_message},
        };
    }
    return j;
}

}  // namespace algolib
