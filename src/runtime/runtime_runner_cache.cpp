#include "algolib/runtime/runtime_runner_cache.h"

#include <utility>

#include "algolib/core/backend_type.h"
#include "algolib/core/error_code.h"
#include "algolib/io/json_utils.h"
#include "algolib/runtime/python_http_runner_pool.h"

namespace algolib {

// ---------------------------------------------------------------------------
// MakeCacheKey
// ---------------------------------------------------------------------------

std::string RuntimeRunnerCache::MakeCacheKey(const AlgorithmEntry& entry,
                                              const std::string& deploy_id) {
    return entry.key.ToUniqueString() + "|" + deploy_id;
}

std::string RuntimeRunnerCache::MakeCacheKey(const AlgorithmKey& key,
                                              const std::string& deploy_id) {
    return key.ToUniqueString() + "|" + deploy_id;
}

// ---------------------------------------------------------------------------
// GetOrLoad
// ---------------------------------------------------------------------------

Result<std::shared_ptr<IAlgorithmRunner>> RuntimeRunnerCache::GetOrLoad(
    const AlgorithmEntry& entry,
    const RuntimeFactory& factory,
    const std::string& deploy_id) {

    const std::string cache_key   = MakeCacheKey(entry, deploy_id);
    const std::string fingerprint = BuildFingerprint(entry);

    // -----------------------------------------------------------------------
    // Python HTTP Service → 连接池路径
    // -----------------------------------------------------------------------
    if (entry.key.backend_type == BackendType::kPythonHttpService) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto it = python_pool_cache_.find(cache_key);
        if (it != python_pool_cache_.end() && it->second.fingerprint == fingerprint &&
            it->second.runner) {
            // 中文注释：池已存在且 fingerprint 未变，直接返回（Run 时池内部做 Checkout）。
            return it->second.runner;
        }

        // 中文注释：创建新的 PythonHttpRunnerPool。
        // RuntimeFactory::Create(kPythonHttpService) 可能返回普通 PythonHttpRunner 或
        // PythonHttpRunnerPool，取决于 factory 配置。这里统一通过 factory 创建，
        // 若 factory 返回的是 PythonHttpRunnerPool 则直接使用。
        auto runner = factory.Create(entry.key.backend_type);
        if (!runner) {
            return Status::Error(ErrorCode::kUnsupportedBackendType,
                                 "No runner registered for python_http_service.");
        }

        auto load_status = runner->Load(entry);
        if (!load_status.ok()) {
            python_pool_cache_.erase(cache_key);
            return load_status;
        }

        auto shared_runner = std::shared_ptr<IAlgorithmRunner>(std::move(runner));
        python_pool_cache_[cache_key] = CachedRunner{fingerprint, shared_runner};
        return shared_runner;
    }

    // -----------------------------------------------------------------------
    // ONNX → 原有逻辑
    // -----------------------------------------------------------------------
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = onnx_cache_.find(cache_key);
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
        onnx_cache_.erase(cache_key);
        return load_status;
    }

    auto shared_runner = std::shared_ptr<IAlgorithmRunner>(std::move(runner));
    onnx_cache_[cache_key] = CachedRunner{fingerprint, shared_runner};
    return shared_runner;
}

// ---------------------------------------------------------------------------
// IsLoaded
// ---------------------------------------------------------------------------

bool RuntimeRunnerCache::IsLoaded(const AlgorithmEntry& entry,
                                   const std::string& deploy_id) const {
    const std::string cache_key   = MakeCacheKey(entry, deploy_id);
    const std::string fingerprint = BuildFingerprint(entry);
    std::lock_guard<std::mutex> lock(mutex_);

    if (entry.key.backend_type == BackendType::kPythonHttpService) {
        auto it = python_pool_cache_.find(cache_key);
        return it != python_pool_cache_.end() &&
               it->second.fingerprint == fingerprint &&
               it->second.runner != nullptr;
    }

    auto it = onnx_cache_.find(cache_key);
    return it != onnx_cache_.end() && it->second.fingerprint == fingerprint &&
           it->second.runner != nullptr;
}

// ---------------------------------------------------------------------------
// GetCachedRunner
// ---------------------------------------------------------------------------

std::shared_ptr<IAlgorithmRunner> RuntimeRunnerCache::GetCachedRunner(
    const AlgorithmEntry& entry, const std::string& deploy_id) const {
    const std::string cache_key   = MakeCacheKey(entry, deploy_id);
    const std::string fingerprint = BuildFingerprint(entry);
    std::lock_guard<std::mutex> lock(mutex_);

    if (entry.key.backend_type == BackendType::kPythonHttpService) {
        auto it = python_pool_cache_.find(cache_key);
        if (it != python_pool_cache_.end() && it->second.fingerprint == fingerprint &&
            it->second.runner) {
            return it->second.runner;
        }
        return nullptr;
    }

    auto it = onnx_cache_.find(cache_key);
    if (it != onnx_cache_.end() && it->second.fingerprint == fingerprint &&
        it->second.runner) {
        return it->second.runner;
    }
    return nullptr;
}

// ---------------------------------------------------------------------------
// Invalidate（清除某 AlgorithmKey 的所有缓存条目）
// ---------------------------------------------------------------------------

void RuntimeRunnerCache::Invalidate(const AlgorithmKey& key) {
    const std::string prefix = key.ToUniqueString() + "|";
    std::lock_guard<std::mutex> lock(mutex_);
    for (auto it = onnx_cache_.begin(); it != onnx_cache_.end();) {
        if (it->first.rfind(prefix, 0) == 0) {
            it = onnx_cache_.erase(it);
        } else {
            ++it;
        }
    }
    for (auto it = python_pool_cache_.begin(); it != python_pool_cache_.end();) {
        if (it->first.rfind(prefix, 0) == 0) {
            it = python_pool_cache_.erase(it);
        } else {
            ++it;
        }
    }
}

// ---------------------------------------------------------------------------
// InvalidateDeployment（只清除指定 deploy_id 的缓存）
// ---------------------------------------------------------------------------

void RuntimeRunnerCache::InvalidateDeployment(const AlgorithmKey& key,
                                               const std::string& deploy_id) {
    const std::string cache_key = MakeCacheKey(key, deploy_id);
    std::lock_guard<std::mutex> lock(mutex_);
    onnx_cache_.erase(cache_key);
    python_pool_cache_.erase(cache_key);
}

// ---------------------------------------------------------------------------
// Clear / Size
// ---------------------------------------------------------------------------

void RuntimeRunnerCache::Clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    onnx_cache_.clear();
    python_pool_cache_.clear();
}

std::size_t RuntimeRunnerCache::Size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return onnx_cache_.size() + python_pool_cache_.size();
}

std::string RuntimeRunnerCache::BuildFingerprint(const AlgorithmEntry& entry) const {
    return entry.key.ToUniqueString() + "|" + entry.package_root.generic_string() + "|" +
           entry.card_path.generic_string() + "|" + JsonUtils::Dump(ToJson(entry.card));
}

}  // namespace algolib
