#pragma once

#include <filesystem>
#include <map>
#include <mutex>
#include <optional>
#include <shared_mutex>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/schema_validator.h"
#include "algolib/registry/registry_store.h"
#include "algolib/validation/algorithm_card_validator.h"

namespace algolib {

// 中文注释：多维查询过滤条件。所有字段均为可选，未设置表示"不限该维度"。
// 各字段语义：
//   task_family    — 精确匹配 algorithm_card.task_family
//   backend_type   — 精确匹配 backend_type（onnx / python_http_service）
//   capability     — 要求 algorithm_card.capabilities 中至少包含该值
//   node_id        — 要求 deployments 中至少有一条记录的 node_id 等于该值
//   status         — 精确匹配 AlgorithmEntry.status（不设则默认只返回 active）
//   active_only    — true: 只返回 active（优先级高于 status 字段）
//   max_vram_mb    — 资源约束：resource_requirements.min_vram_mb <= max_vram_mb
//                    即"我的显存上限，能运行哪些模型"（min_vram 代表模型最低要求）
//   max_memory_mb  — 资源约束：resource_requirements.min_memory_mb <= max_memory_mb
//   max_cpu_cores  — 资源约束：resource_requirements.min_cpu_cores <= max_cpu_cores
struct AlgorithmQueryFilter {
    std::optional<std::string>       task_family;
    std::optional<BackendType>       backend_type;
    std::optional<std::string>       capability;
    std::optional<std::string>       function_id;
    std::optional<std::string>       function_code;
    std::optional<std::string>       node_id;
    std::optional<AlgorithmStatus>   status;
    bool                             active_only  = true;
    std::optional<int>               max_vram_mb;
    std::optional<int>               max_memory_mb;
    std::optional<int>               max_cpu_cores;
};

// 中文注释: AlgorithmRegistry 提供注册、校验、启停、查询和部署管理能力。
//
// 并发语义：
//   - 读操作以 shared_lock 保护，并返回 AlgorithmEntry 的值副本；执行中的请求因此
//     持有稳定快照，不会被 /reload 或生命周期变更破坏。
//   - 写操作以 unique_lock 串行，并先持久化候选副本，成功后再原子替换内存 entries_；
//     持久化失败不会污染正在提供服务的内存快照。
class AlgorithmRegistry {
public:
    explicit AlgorithmRegistry(std::filesystem::path registry_path);

    // 从磁盘构造新快照后一次性替换当前 entries_。加载失败时保留旧快照。
    Status Reload();

    Result<AlgorithmEntry> Register(const std::filesystem::path& package_or_card_path);
    Result<AlgorithmEntry> Validate(const AlgorithmKey& key);
    Result<AlgorithmEntry> Activate(const AlgorithmKey& key);
    Result<AlgorithmEntry> Disable(const AlgorithmKey& key);
    Result<AlgorithmEntry> Delete(const AlgorithmKey& key);
    Result<AlgorithmEntry> Get(const AlgorithmKey& key) const;

    Status ValidateInputPayload(const AlgorithmKey& key,
                                const nlohmann::json& input_json) const;
    Status ValidateOutputPayload(const AlgorithmKey& key,
                                 const nlohmann::json& output_json) const;

    // 对已经取得的 AlgorithmEntry 快照做校验，避免一次执行请求跨越 registry 更新。
    Status ValidateInputPayload(const AlgorithmEntry& entry,
                                const nlohmann::json& input_json) const;
    Status ValidateOutputPayload(const AlgorithmEntry& entry,
                                 const nlohmann::json& output_json) const;

    // 原有接口（保持兼容）
    std::vector<AlgorithmEntry>  List(bool include_deleted = false) const;
    std::vector<nlohmann::json>  ListAgentViews(bool active_only = true) const;

    // 中文注释：多维过滤查询接口。
    // Query          — 返回满足条件的完整 AlgorithmEntry 列表。
    // QueryAgentViews — 返回满足条件的 agent_view JSON 列表（HTTP / CLI 对外输出使用）。
    std::vector<AlgorithmEntry>  Query(const AlgorithmQueryFilter& filter) const;
    std::vector<nlohmann::json>  QueryAgentViews(const AlgorithmQueryFilter& filter) const;

    // 中文注释：部署实例管理接口。
    // AddDeployment      — 向指定算法的注册表条目追加一条部署记录；
    //                      deploy_id 在同一 AlgorithmKey 下必须唯一。
    //                      若 spec.deployed_at 为空，自动填入当前 UTC 时间戳。
    // RemoveDeployment   — 按 deploy_id 删除指定部署记录。
    // UpdateDeploymentStatus — 更新指定 deploy_id 的状态、状态附加信息及可选字段。
    //                      local_model_path 非空时同步更新（http_pull 后回写文件路径）。
    Result<AlgorithmEntry> AddDeployment(const AlgorithmKey& key,
                                         const DeploymentSpec& spec);
    Result<AlgorithmEntry> RemoveDeployment(const AlgorithmKey& key,
                                             const std::string& deploy_id);
    Result<AlgorithmEntry> UpdateDeploymentStatus(const AlgorithmKey& key,
                                                   const std::string& deploy_id,
                                                   DeploymentStatus new_status,
                                                   const std::string& status_message,
                                                   const std::string& updated_at,
                                                   const std::string& local_model_path = {});

    const std::filesystem::path& registry_path() const;

private:
    using EntryMap = std::map<AlgorithmKey, AlgorithmEntry>;

    // 调用方必须已持有 mutex_ 的 unique_lock。
    Status PersistLocked(const EntryMap& entries) const;
    AlgorithmEntry BuildEntry(const ValidatedAlgorithmPackage& validated_package,
                              AlgorithmStatus effective_status) const;

    RegistryStore store_;
    AlgorithmCardValidator validator_;
    SchemaValidator schema_validator_;
    // 序列化 registry.json 的读写及“读盘→提交快照”过程，避免 reload 覆盖并发持久化更新。
    mutable std::mutex persistence_mutex_;
    mutable std::shared_mutex mutex_;
    EntryMap entries_;
};

}  // namespace algolib
