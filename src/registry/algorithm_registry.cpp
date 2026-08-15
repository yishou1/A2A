#include "algolib/registry/algorithm_registry.h"

#include <algorithm>
#include <chrono>
#include <ctime>
#include <utility>

namespace algolib {
namespace {

Status EnsureActivatable(AlgorithmStatus status) {
    if (status == AlgorithmStatus::kValidated || status == AlgorithmStatus::kDisabled ||
        status == AlgorithmStatus::kActive) {
        return Status::Ok();
    }
    return Status::Error(
        ErrorCode::kStatusTransitionInvalid,
        "Only validated or disabled algorithms can be activated.");
}

Status EnsureDisableable(AlgorithmStatus status) {
    if (status == AlgorithmStatus::kValidated || status == AlgorithmStatus::kActive ||
        status == AlgorithmStatus::kDisabled) {
        return Status::Ok();
    }
    return Status::Error(
        ErrorCode::kStatusTransitionInvalid,
        "Only validated or active algorithms can be disabled.");
}

Status EnsureValidatable(AlgorithmStatus status) {
    if (status == AlgorithmStatus::kDeleted) {
        return Status::Error(
            ErrorCode::kStatusTransitionInvalid,
            "Deleted algorithms cannot be re-validated.");
    }
    return Status::Ok();
}

Status EnsureDeletable(AlgorithmStatus status) {
    if (status == AlgorithmStatus::kDeleted) {
        return Status::Ok();
    }
    return Status::Ok();
}

}  // namespace

AlgorithmRegistry::AlgorithmRegistry(std::filesystem::path registry_path)
    : store_(std::move(registry_path)) {}

Status AlgorithmRegistry::Reload() {
    return store_.Load(&entries_);
}

Result<AlgorithmEntry> AlgorithmRegistry::Register(
    const std::filesystem::path& package_or_card_path) {
    auto validated_result = validator_.ValidateFromPath(package_or_card_path);
    if (!validated_result.ok()) {
        return validated_result.status();
    }

    AlgorithmEntry new_entry =
        BuildEntry(validated_result.value(), AlgorithmStatus::kValidated);
    if (entries_.find(new_entry.key) != entries_.end()) {
        return Status::Error(
            ErrorCode::kRegistryConflict,
            "An algorithm with the same algorithm_id, version and backend_type already exists: " +
                new_entry.key.ToUniqueString());
    }

    entries_.insert_or_assign(new_entry.key, new_entry);
    auto persist_status = Persist();
    if (!persist_status.ok()) {
        return persist_status;
    }

    return new_entry;
}

Result<AlgorithmEntry> AlgorithmRegistry::Validate(const AlgorithmKey& key) {
    auto find_result = FindMutable(key);
    if (!find_result.ok()) {
        return find_result.status();
    }

    AlgorithmEntry* entry = find_result.value();
    auto validate_status = EnsureValidatable(entry->status);
    if (!validate_status.ok()) {
        return validate_status;
    }

    auto validated_result = validator_.ValidateFromPath(entry->card_path);
    if (!validated_result.ok()) {
        return validated_result.status();
    }

    const AlgorithmStatus effective_status =
        entry->status == AlgorithmStatus::kDraft ? AlgorithmStatus::kValidated : entry->status;
    *entry = BuildEntry(validated_result.value(), effective_status);

    auto persist_status = Persist();
    if (!persist_status.ok()) {
        return persist_status;
    }

    return *entry;
}

Result<AlgorithmEntry> AlgorithmRegistry::Activate(const AlgorithmKey& key) {
    auto find_result = FindMutable(key);
    if (!find_result.ok()) {
        return find_result.status();
    }

    AlgorithmEntry* entry = find_result.value();
    auto activatable_status = EnsureActivatable(entry->status);
    if (!activatable_status.ok()) {
        return activatable_status;
    }

    entry->status = AlgorithmStatus::kActive;
    auto persist_status = Persist();
    if (!persist_status.ok()) {
        return persist_status;
    }

    return *entry;
}

Result<AlgorithmEntry> AlgorithmRegistry::Disable(const AlgorithmKey& key) {
    auto find_result = FindMutable(key);
    if (!find_result.ok()) {
        return find_result.status();
    }

    AlgorithmEntry* entry = find_result.value();
    auto disableable_status = EnsureDisableable(entry->status);
    if (!disableable_status.ok()) {
        return disableable_status;
    }

    entry->status = AlgorithmStatus::kDisabled;
    auto persist_status = Persist();
    if (!persist_status.ok()) {
        return persist_status;
    }

    return *entry;
}

Result<AlgorithmEntry> AlgorithmRegistry::Delete(const AlgorithmKey& key) {
    auto find_result = FindMutable(key);
    if (!find_result.ok()) {
        return find_result.status();
    }

    AlgorithmEntry* entry = find_result.value();
    auto deletable_status = EnsureDeletable(entry->status);
    if (!deletable_status.ok()) {
        return deletable_status;
    }

    entry->status = AlgorithmStatus::kDeleted;
    auto persist_status = Persist();
    if (!persist_status.ok()) {
        return persist_status;
    }

    return *entry;
}

Result<AlgorithmEntry> AlgorithmRegistry::Get(const AlgorithmKey& key) const {
    auto find_result = Find(key);
    if (!find_result.ok()) {
        return find_result.status();
    }
    return *find_result.value();
}

Status AlgorithmRegistry::ValidateInputPayload(const AlgorithmKey& key,
                                               const nlohmann::json& input_json) const {
    auto find_result = Find(key);
    if (!find_result.ok()) {
        return find_result.status();
    }
    return schema_validator_.ValidateInputForEntry(*find_result.value(), input_json);
}

Status AlgorithmRegistry::ValidateOutputPayload(const AlgorithmKey& key,
                                                const nlohmann::json& output_json) const {
    auto find_result = Find(key);
    if (!find_result.ok()) {
        return find_result.status();
    }
    return schema_validator_.ValidateOutputForEntry(*find_result.value(), output_json);
}

std::vector<AlgorithmEntry> AlgorithmRegistry::List(bool include_deleted) const {
    std::vector<AlgorithmEntry> result;
    for (const auto& [key, entry] : entries_) {
        (void)key;
        if (!include_deleted && entry.status == AlgorithmStatus::kDeleted) {
            continue;
        }
        result.push_back(entry);
    }
    return result;
}

std::vector<nlohmann::json> AlgorithmRegistry::ListAgentViews(bool active_only) const {
    std::vector<nlohmann::json> result;
    for (const auto& [key, entry] : entries_) {
        (void)key;
        if (entry.status == AlgorithmStatus::kDeleted) {
            continue;
        }
        if (active_only && entry.status != AlgorithmStatus::kActive) {
            continue;
        }
        result.push_back(ToAgentViewJson(entry));
    }
    return result;
}

// 中文注释：FilterMatches — 判断单个条目是否满足所有过滤条件。
// 各维度按照"不设则不过滤，已设则必须匹配"的语义执行，
// active_only 优先于 status 字段（active_only=true 时忽略 status）。
namespace {

bool FilterMatches(const AlgorithmEntry& entry, const AlgorithmQueryFilter& f) {
    // 1. 状态过滤：active_only 优先
    if (entry.status == AlgorithmStatus::kDeleted) {
        return false;
    }
    if (f.active_only) {
        if (entry.status != AlgorithmStatus::kActive) {
            return false;
        }
    } else if (f.status.has_value()) {
        if (entry.status != f.status.value()) {
            return false;
        }
    }

    // 2. backend_type 精确匹配
    if (f.backend_type.has_value()) {
        if (entry.key.backend_type != f.backend_type.value()) {
            return false;
        }
    }

    // 3. task_family 精确匹配（大小写敏感）
    if (f.task_family.has_value()) {
        if (entry.card.task_family != f.task_family.value()) {
            return false;
        }
    }

    // 4. capability 包含匹配：capabilities 中至少有一个与过滤值相等
    if (f.capability.has_value()) {
        const auto& caps = entry.card.capabilities;
        const bool found = std::find(caps.begin(), caps.end(), f.capability.value()) != caps.end();
        if (!found) {
            return false;
        }
    }

    // 5. node_id 匹配：deployments 中至少有一条记录的 node_id 相等
    if (f.node_id.has_value()) {
        const auto& deps = entry.deployments;
        const bool found = std::find_if(deps.begin(), deps.end(),
                                        [&](const DeploymentSpec& d) {
                                            return d.node_id == f.node_id.value();
                                        }) != deps.end();
        if (!found) {
            return false;
        }
    }

    // 6. 资源约束过滤（min_xxx 是模型最低要求；调用方给出的是上限）
    //    语义：模型要求 <= 调用方上限，表示资源足够
    const auto& rr = entry.card.resource_requirements;
    if (f.max_vram_mb.has_value() && rr.has_value() && rr->min_vram_mb.has_value()) {
        if (rr->min_vram_mb.value() > f.max_vram_mb.value()) {
            return false;
        }
    }
    if (f.max_memory_mb.has_value() && rr.has_value() && rr->min_memory_mb.has_value()) {
        if (rr->min_memory_mb.value() > f.max_memory_mb.value()) {
            return false;
        }
    }
    if (f.max_cpu_cores.has_value() && rr.has_value() && rr->min_cpu_cores.has_value()) {
        if (rr->min_cpu_cores.value() > f.max_cpu_cores.value()) {
            return false;
        }
    }

    return true;
}

}  // namespace (anonymous)

std::vector<AlgorithmEntry> AlgorithmRegistry::Query(const AlgorithmQueryFilter& filter) const {
    std::vector<AlgorithmEntry> result;
    for (const auto& [key, entry] : entries_) {
        (void)key;
        if (FilterMatches(entry, filter)) {
            result.push_back(entry);
        }
    }
    return result;
}

std::vector<nlohmann::json> AlgorithmRegistry::QueryAgentViews(
    const AlgorithmQueryFilter& filter) const {
    std::vector<nlohmann::json> result;
    for (const auto& [key, entry] : entries_) {
        (void)key;
        if (FilterMatches(entry, filter)) {
            result.push_back(ToAgentViewJson(entry));
        }
    }
    return result;
}

Result<AlgorithmEntry> AlgorithmRegistry::AddDeployment(const AlgorithmKey& key,
                                                         const DeploymentSpec& spec) {
    auto find_result = FindMutable(key);
    if (!find_result.ok()) {
        return find_result.status();
    }
    AlgorithmEntry* entry = find_result.value();

    // 中文注释：deploy_id 在同一 AlgorithmKey 下必须唯一。
    if (spec.deploy_id.empty()) {
        return Status::Error(ErrorCode::kInvalidArgument,
                             "DeploymentSpec.deploy_id must not be empty.");
    }
    for (const auto& existing : entry->deployments) {
        if (existing.deploy_id == spec.deploy_id) {
            return Status::Error(
                ErrorCode::kRegistryConflict,
                "A deployment with deploy_id=" + spec.deploy_id +
                    " already exists for algorithm " + key.ToUniqueString() + ".");
        }
    }

    // 中文注释：若调用方未提供 deployed_at，自动填入当前 UTC 时间戳。
    DeploymentSpec effective_spec = spec;
    if (effective_spec.deployed_at.empty()) {
        const auto now = std::chrono::system_clock::now();
        const auto now_t = std::chrono::system_clock::to_time_t(now);
        char buf[32] = {};
        std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&now_t));
        effective_spec.deployed_at = buf;
    }

    entry->deployments.push_back(effective_spec);
    auto persist_status = Persist();
    if (!persist_status.ok()) {
        return persist_status;
    }
    return *entry;
}

Result<AlgorithmEntry> AlgorithmRegistry::RemoveDeployment(const AlgorithmKey& key,
                                                             const std::string& deploy_id) {
    auto find_result = FindMutable(key);
    if (!find_result.ok()) {
        return find_result.status();
    }
    AlgorithmEntry* entry = find_result.value();

    auto it = std::find_if(entry->deployments.begin(), entry->deployments.end(),
                           [&deploy_id](const DeploymentSpec& s) {
                               return s.deploy_id == deploy_id;
                           });
    if (it == entry->deployments.end()) {
        return Status::Error(
            ErrorCode::kAlgorithmNotFound,
            "Deployment deploy_id=" + deploy_id +
                " not found for algorithm " + key.ToUniqueString() + ".");
    }

    entry->deployments.erase(it);
    auto persist_status = Persist();
    if (!persist_status.ok()) {
        return persist_status;
    }
    return *entry;
}

Result<AlgorithmEntry> AlgorithmRegistry::UpdateDeploymentStatus(
    const AlgorithmKey& key,
    const std::string& deploy_id,
    DeploymentStatus new_status,
    const std::string& status_message,
    const std::string& updated_at,
    const std::string& local_model_path) {
    auto find_result = FindMutable(key);
    if (!find_result.ok()) {
        return find_result.status();
    }
    AlgorithmEntry* entry = find_result.value();

    auto it = std::find_if(entry->deployments.begin(), entry->deployments.end(),
                           [&deploy_id](const DeploymentSpec& s) {
                               return s.deploy_id == deploy_id;
                           });
    if (it == entry->deployments.end()) {
        return Status::Error(
            ErrorCode::kAlgorithmNotFound,
            "Deployment deploy_id=" + deploy_id +
                " not found for algorithm " + key.ToUniqueString() + ".");
    }

    it->deploy_status = new_status;
    it->status_message = status_message;
    // 中文注释：updated_at 为空时自动填入当前 UTC 时间戳。
    if (!updated_at.empty()) {
        it->updated_at = updated_at;
    } else {
        const auto now = std::chrono::system_clock::now();
        const auto now_t = std::chrono::system_clock::to_time_t(now);
        char buf[32] = {};
        std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&now_t));
        it->updated_at = buf;
    }
    // 中文注释：local_model_path 非空时同步更新（http_pull 拉取文件后记录本地路径）。
    if (!local_model_path.empty()) {
        it->local_model_path = local_model_path;
    }

    auto persist_status = Persist();
    if (!persist_status.ok()) {
        return persist_status;
    }
    return *entry;
}

const std::filesystem::path& AlgorithmRegistry::registry_path() const {
    return store_.registry_path();
}

Status AlgorithmRegistry::Persist() const {
    return store_.Save(entries_);
}

Result<AlgorithmEntry*> AlgorithmRegistry::FindMutable(const AlgorithmKey& key) {
    auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }
    return &it->second;
}

Result<const AlgorithmEntry*> AlgorithmRegistry::Find(const AlgorithmKey& key) const {
    auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }
    return &it->second;
}

AlgorithmEntry AlgorithmRegistry::BuildEntry(
    const ValidatedAlgorithmPackage& validated_package,
    AlgorithmStatus effective_status) const {
    AlgorithmEntry entry;
    entry.key.algorithm_id = validated_package.card.algorithm_id;
    entry.key.version = validated_package.card.version;
    entry.key.backend_type = validated_package.card.backend_type;
    entry.status = effective_status;
    entry.package_root = validated_package.package_root;
    entry.card_path = validated_package.card_path;
    entry.card = validated_package.card;
    entry.input_schema_summary = validated_package.input_schema_summary;
    entry.output_schema_summary = validated_package.output_schema_summary;
    return entry;
}

}  // namespace algolib
