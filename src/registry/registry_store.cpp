#include "algolib/registry/registry_store.h"

#include "algolib/io/file_utils.h"
#include "algolib/io/json_utils.h"

namespace algolib {

RegistryStore::RegistryStore(std::filesystem::path registry_path)
    : registry_path_(std::move(registry_path)) {}

Status RegistryStore::Load(std::map<AlgorithmKey, AlgorithmEntry>* entries) const {
    entries->clear();
    if (!std::filesystem::exists(registry_path_)) {
        return Status::Ok();
    }

    auto json_result = JsonUtils::ReadJsonFile(registry_path_);
    if (!json_result.ok()) {
        return json_result.status();
    }

    const auto& root = json_result.value();
    if (!root.is_object()) {
        return Status::Error(ErrorCode::kRegistryStoreError,
                             "Registry file root must be a JSON object.");
    }

    if (!root.contains("entries") || !root.at("entries").is_array()) {
        return Status::Error(ErrorCode::kRegistryStoreError,
                             "Registry file must contain an entries array.");
    }

    for (const auto& entry_json : root.at("entries")) {
        auto entry_result = AlgorithmEntryFromJson(entry_json);
        if (!entry_result.ok()) {
            return entry_result.status();
        }
        entries->insert_or_assign(entry_result.value().key, entry_result.value());
    }

    return Status::Ok();
}

Status RegistryStore::Save(const std::map<AlgorithmKey, AlgorithmEntry>& entries) const {
    nlohmann::json root;
    root["schema_version"] = "phase_0_1";
    root["entries"] = nlohmann::json::array();
    for (const auto& [key, entry] : entries) {
        (void)key;
        root["entries"].push_back(ToJson(entry));
    }

    return JsonUtils::WriteJsonFile(registry_path_, root);
}

const std::filesystem::path& RegistryStore::registry_path() const {
    return registry_path_;
}

}  // namespace algolib
