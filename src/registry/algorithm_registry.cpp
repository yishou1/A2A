#include "algolib/registry/algorithm_registry.h"

#include <algorithm>
#include <chrono>
#include <ctime>
#include <mutex>
#include <shared_mutex>
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
    (void)status;
    return Status::Ok();
}

std::string CurrentUtcTimestamp() {
    const auto now = std::chrono::system_clock::now();
    const auto now_t = std::chrono::system_clock::to_time_t(now);
    std::tm utc{};
#ifdef _WIN32
    gmtime_s(&utc, &now_t);
#else
    gmtime_r(&now_t, &utc);
#endif
    char buf[32] = {};
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &utc);
    return buf;
}

}  // namespace

AlgorithmRegistry::AlgorithmRegistry(std::filesystem::path registry_path)
    : store_(std::move(registry_path)) {}

Status AlgorithmRegistry::Reload() {
    // 与持久化更新串行，避免“旧磁盘快照读完后覆盖新写入”的 lost update。
    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    EntryMap loaded_entries;
    const Status load_status = store_.Load(&loaded_entries);
    if (!load_status.ok()) {
        return load_status;
    }

    std::unique_lock<std::shared_mutex> lock(mutex_);
    entries_.swap(loaded_entries);
    return Status::Ok();
}

Result<AlgorithmEntry> AlgorithmRegistry::Register(
    const std::filesystem::path& package_or_card_path) {
    // 包校验可能涉及磁盘与远端服务，不能阻塞所有读请求。
    auto validated_result = validator_.ValidateFromPath(package_or_card_path);
    if (!validated_result.ok()) {
        return validated_result.status();
    }

    AlgorithmEntry new_entry =
        BuildEntry(validated_result.value(), AlgorithmStatus::kValidated);

    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    std::unique_lock<std::shared_mutex> lock(mutex_);
    if (entries_.find(new_entry.key) != entries_.end()) {
        return Status::Error(
            ErrorCode::kRegistryConflict,
            "An algorithm with the same algorithm_id, version and backend_type already exists: " +
                new_entry.key.ToUniqueString());
    }

    EntryMap candidate = entries_;
    candidate.insert_or_assign(new_entry.key, new_entry);
    const Status persist_status = PersistLocked(candidate);
    if (!persist_status.ok()) {
        return persist_status;
    }
    entries_.swap(candidate);
    return new_entry;
}

Result<AlgorithmEntry> AlgorithmRegistry::Validate(const AlgorithmKey& key) {
    AlgorithmEntry current_entry;
    {
        std::shared_lock<std::shared_mutex> lock(mutex_);
        const auto it = entries_.find(key);
        if (it == entries_.end()) {
            return Status::Error(ErrorCode::kAlgorithmNotFound,
                                 "Algorithm not found: " + key.ToUniqueString());
        }
        current_entry = it->second;
    }

    const Status validatable_status = EnsureValidatable(current_entry.status);
    if (!validatable_status.ok()) {
        return validatable_status;
    }

    // 校验 package 时释放 registry 锁，避免阻塞在线推理。
    auto validated_result = validator_.ValidateFromPath(current_entry.card_path);
    if (!validated_result.ok()) {
        return validated_result.status();
    }

    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    std::unique_lock<std::shared_mutex> lock(mutex_);
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }

    // 管理操作期间条目可能变化，基于最新快照重新确认状态和部署信息。
    const Status recheck_status = EnsureValidatable(it->second.status);
    if (!recheck_status.ok()) {
        return recheck_status;
    }

    const AlgorithmStatus effective_status =
        it->second.status == AlgorithmStatus::kDraft ? AlgorithmStatus::kValidated
                                                       : it->second.status;
    auto deployments = it->second.deployments;
    AlgorithmEntry refreshed = BuildEntry(validated_result.value(), effective_status);
    refreshed.deployments = std::move(deployments);

    EntryMap candidate = entries_;
    candidate.insert_or_assign(key, refreshed);
    const Status persist_status = PersistLocked(candidate);
    if (!persist_status.ok()) {
        return persist_status;
    }
    entries_.swap(candidate);
    return refreshed;
}

Result<AlgorithmEntry> AlgorithmRegistry::Activate(const AlgorithmKey& key) {
    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    std::unique_lock<std::shared_mutex> lock(mutex_);
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }
    const Status activatable_status = EnsureActivatable(it->second.status);
    if (!activatable_status.ok()) {
        return activatable_status;
    }

    EntryMap candidate = entries_;
    auto candidate_it = candidate.find(key);
    candidate_it->second.status = AlgorithmStatus::kActive;
    const Status persist_status = PersistLocked(candidate);
    if (!persist_status.ok()) {
        return persist_status;
    }
    const AlgorithmEntry result = candidate_it->second;
    entries_.swap(candidate);
    return result;
}

Result<AlgorithmEntry> AlgorithmRegistry::Disable(const AlgorithmKey& key) {
    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    std::unique_lock<std::shared_mutex> lock(mutex_);
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }
    const Status disableable_status = EnsureDisableable(it->second.status);
    if (!disableable_status.ok()) {
        return disableable_status;
    }

    EntryMap candidate = entries_;
    auto candidate_it = candidate.find(key);
    candidate_it->second.status = AlgorithmStatus::kDisabled;
    const Status persist_status = PersistLocked(candidate);
    if (!persist_status.ok()) {
        return persist_status;
    }
    const AlgorithmEntry result = candidate_it->second;
    entries_.swap(candidate);
    return result;
}

Result<AlgorithmEntry> AlgorithmRegistry::Delete(const AlgorithmKey& key) {
    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    std::unique_lock<std::shared_mutex> lock(mutex_);
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }
    const Status deletable_status = EnsureDeletable(it->second.status);
    if (!deletable_status.ok()) {
        return deletable_status;
    }

    EntryMap candidate = entries_;
    auto candidate_it = candidate.find(key);
    candidate_it->second.status = AlgorithmStatus::kDeleted;
    const Status persist_status = PersistLocked(candidate);
    if (!persist_status.ok()) {
        return persist_status;
    }
    const AlgorithmEntry result = candidate_it->second;
    entries_.swap(candidate);
    return result;
}

Result<AlgorithmEntry> AlgorithmRegistry::Get(const AlgorithmKey& key) const {
    std::shared_lock<std::shared_mutex> lock(mutex_);
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }
    return it->second;
}

Status AlgorithmRegistry::ValidateInputPayload(const AlgorithmKey& key,
                                               const nlohmann::json& input_json) const {
    auto entry_result = Get(key);
    if (!entry_result.ok()) {
        return entry_result.status();
    }
    return ValidateInputPayload(entry_result.value(), input_json);
}

Status AlgorithmRegistry::ValidateOutputPayload(const AlgorithmKey& key,
                                                const nlohmann::json& output_json) const {
    auto entry_result = Get(key);
    if (!entry_result.ok()) {
        return entry_result.status();
    }
    return ValidateOutputPayload(entry_result.value(), output_json);
}

Status AlgorithmRegistry::ValidateInputPayload(const AlgorithmEntry& entry,
                                               const nlohmann::json& input_json) const {
    return schema_validator_.ValidateInputForEntry(entry, input_json);
}

Status AlgorithmRegistry::ValidateOutputPayload(const AlgorithmEntry& entry,
                                                const nlohmann::json& output_json) const {
    return schema_validator_.ValidateOutputForEntry(entry, output_json);
}

std::vector<AlgorithmEntry> AlgorithmRegistry::List(bool include_deleted) const {
    std::shared_lock<std::shared_mutex> lock(mutex_);
    std::vector<AlgorithmEntry> result;
    result.reserve(entries_.size());
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
    std::shared_lock<std::shared_mutex> lock(mutex_);
    std::vector<nlohmann::json> result;
    result.reserve(entries_.size());
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

    if (f.function_id.has_value() || f.function_code.has_value()) {
        const bool found = std::find_if(
            entry.card.operational_functions.begin(),
            entry.card.operational_functions.end(),
            [&](const OperationalFunctionSpec& function) {
                const bool id_matches = !f.function_id.has_value() ||
                                        function.function_id == f.function_id.value();
                const bool code_matches = !f.function_code.has_value() ||
                                          function.function_code == f.function_code.value();
                return id_matches && code_matches;
            }) != entry.card.operational_functions.end();
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
    std::shared_lock<std::shared_mutex> lock(mutex_);
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
    std::shared_lock<std::shared_mutex> lock(mutex_);
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
    if (spec.deploy_id.empty()) {
        return Status::Error(ErrorCode::kInvalidArgument,
                             "DeploymentSpec.deploy_id must not be empty.");
    }

    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    std::unique_lock<std::shared_mutex> lock(mutex_);
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }
    for (const auto& existing : it->second.deployments) {
        if (existing.deploy_id == spec.deploy_id) {
            return Status::Error(
                ErrorCode::kRegistryConflict,
                "A deployment with deploy_id=" + spec.deploy_id +
                    " already exists for algorithm " + key.ToUniqueString() + ".");
        }
    }

    DeploymentSpec effective_spec = spec;
    if (effective_spec.deployed_at.empty()) {
        effective_spec.deployed_at = CurrentUtcTimestamp();
    }

    EntryMap candidate = entries_;
    auto candidate_it = candidate.find(key);
    candidate_it->second.deployments.push_back(std::move(effective_spec));
    const Status persist_status = PersistLocked(candidate);
    if (!persist_status.ok()) {
        return persist_status;
    }
    const AlgorithmEntry result = candidate_it->second;
    entries_.swap(candidate);
    return result;
}

Result<AlgorithmEntry> AlgorithmRegistry::RemoveDeployment(const AlgorithmKey& key,
                                                             const std::string& deploy_id) {
    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    std::unique_lock<std::shared_mutex> lock(mutex_);
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }

    EntryMap candidate = entries_;
    auto candidate_it = candidate.find(key);
    auto deployment_it = std::find_if(candidate_it->second.deployments.begin(),
                                      candidate_it->second.deployments.end(),
                                      [&deploy_id](const DeploymentSpec& s) {
                                          return s.deploy_id == deploy_id;
                                      });
    if (deployment_it == candidate_it->second.deployments.end()) {
        return Status::Error(
            ErrorCode::kAlgorithmNotFound,
            "Deployment deploy_id=" + deploy_id +
                " not found for algorithm " + key.ToUniqueString() + ".");
    }

    candidate_it->second.deployments.erase(deployment_it);
    const Status persist_status = PersistLocked(candidate);
    if (!persist_status.ok()) {
        return persist_status;
    }
    const AlgorithmEntry result = candidate_it->second;
    entries_.swap(candidate);
    return result;
}

Result<AlgorithmEntry> AlgorithmRegistry::UpdateDeploymentStatus(
    const AlgorithmKey& key,
    const std::string& deploy_id,
    DeploymentStatus new_status,
    const std::string& status_message,
    const std::string& updated_at,
    const std::string& local_model_path) {
    std::lock_guard<std::mutex> persistence_lock(persistence_mutex_);
    std::unique_lock<std::shared_mutex> lock(mutex_);
    const auto it = entries_.find(key);
    if (it == entries_.end()) {
        return Status::Error(ErrorCode::kAlgorithmNotFound,
                             "Algorithm not found: " + key.ToUniqueString());
    }

    EntryMap candidate = entries_;
    auto candidate_it = candidate.find(key);
    auto deployment_it = std::find_if(candidate_it->second.deployments.begin(),
                                      candidate_it->second.deployments.end(),
                                      [&deploy_id](const DeploymentSpec& s) {
                                          return s.deploy_id == deploy_id;
                                      });
    if (deployment_it == candidate_it->second.deployments.end()) {
        return Status::Error(
            ErrorCode::kAlgorithmNotFound,
            "Deployment deploy_id=" + deploy_id +
                " not found for algorithm " + key.ToUniqueString() + ".");
    }

    deployment_it->deploy_status = new_status;
    deployment_it->status_message = status_message;
    deployment_it->updated_at = updated_at.empty() ? CurrentUtcTimestamp() : updated_at;
    if (!local_model_path.empty()) {
        deployment_it->local_model_path = local_model_path;
    }

    const Status persist_status = PersistLocked(candidate);
    if (!persist_status.ok()) {
        return persist_status;
    }
    const AlgorithmEntry result = candidate_it->second;
    entries_.swap(candidate);
    return result;
}

const std::filesystem::path& AlgorithmRegistry::registry_path() const {
    return store_.registry_path();
}

Status AlgorithmRegistry::PersistLocked(const EntryMap& entries) const {
    return store_.Save(entries);
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
