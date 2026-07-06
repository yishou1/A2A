#include "algolib/runtime/runtime_runner_cache.h"

#include <utility>

#include "algolib/core/backend_type.h"
#include "algolib/core/error_code.h"
#include "algolib/io/json_utils.h"

namespace algolib {

Result<std::shared_ptr<IAlgorithmRunner>> RuntimeRunnerCache::GetOrLoad(
    const AlgorithmEntry& entry,
    const RuntimeFactory& factory) {
    if (entry.key.backend_type != BackendType::kOnnx) {
        auto runner = factory.Create(entry.key.backend_type);
        if (!runner) {
            return Status::Error(ErrorCode::kUnsupportedBackendType,
                                 "No runtime runner is registered for backend_type=" +
                                     ToString(entry.key.backend_type) + ".");
        }
        auto load_status = runner->Load(entry);
        if (!load_status.ok()) {
            return load_status;
        }
        return std::shared_ptr<IAlgorithmRunner>(std::move(runner));
    }

    const std::string fingerprint = BuildFingerprint(entry);
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = onnx_cache_.find(entry.key);
    if (it != onnx_cache_.end() && it->second.fingerprint == fingerprint &&
        it->second.runner) {
        return it->second.runner;
    }

    auto runner = factory.Create(entry.key.backend_type);
    if (!runner) {
        return Status::Error(ErrorCode::kUnsupportedBackendType,
                             "No runtime runner is registered for backend_type=" +
                                 ToString(entry.key.backend_type) + ".");
    }

    auto load_status = runner->Load(entry);
    if (!load_status.ok()) {
        onnx_cache_.erase(entry.key);
        return load_status;
    }

    auto shared_runner = std::shared_ptr<IAlgorithmRunner>(std::move(runner));
    onnx_cache_[entry.key] = CachedRunner{fingerprint, shared_runner};
    return shared_runner;
}

void RuntimeRunnerCache::Invalidate(const AlgorithmKey& key) {
    std::lock_guard<std::mutex> lock(mutex_);
    onnx_cache_.erase(key);
}

void RuntimeRunnerCache::Clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    onnx_cache_.clear();
}

std::size_t RuntimeRunnerCache::Size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return onnx_cache_.size();
}

std::string RuntimeRunnerCache::BuildFingerprint(const AlgorithmEntry& entry) const {
    return entry.key.ToUniqueString() + "|" + entry.package_root.generic_string() + "|" +
           entry.card_path.generic_string() + "|" + JsonUtils::Dump(ToJson(entry.card));
}

}  // namespace algolib
