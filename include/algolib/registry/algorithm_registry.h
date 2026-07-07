#pragma once

#include <filesystem>
#include <map>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/schema_validator.h"
#include "algolib/registry/registry_store.h"
#include "algolib/validation/algorithm_card_validator.h"

namespace algolib {

// 中文注释: AlgorithmRegistry 提供 Phase 1 需要的注册、校验、启停和查询能力
class AlgorithmRegistry {
public:
    explicit AlgorithmRegistry(std::filesystem::path registry_path);

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

    std::vector<AlgorithmEntry> List(bool include_deleted = false) const;
    std::vector<nlohmann::json> ListAgentViews(bool active_only = true) const;

    const std::filesystem::path& registry_path() const;

private:
    Status Persist() const;
    Result<AlgorithmEntry*> FindMutable(const AlgorithmKey& key);
    Result<const AlgorithmEntry*> Find(const AlgorithmKey& key) const;
    AlgorithmEntry BuildEntry(const ValidatedAlgorithmPackage& validated_package,
                              AlgorithmStatus effective_status) const;

    RegistryStore store_;
    AlgorithmCardValidator validator_;
    SchemaValidator schema_validator_;
    std::map<AlgorithmKey, AlgorithmEntry> entries_;
};

}  // namespace algolib
