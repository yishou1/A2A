#include "algolib/runtime/runtime_runner_cache.h"

#include <utility>

#include "algolib/core/backend_type.h"
#include "algolib/core/error_code.h"
#include "algolib/io/json_utils.h"

namespace algolib {

std::string RuntimeRunnerCache::MakeCacheKey(const AlgorithmEntry& entry,
                                              const std::string& deploy_id) {
    return entry.key.ToUniqueString() + "|" + deploy_id;
}

std::string RuntimeRunnerCache::MakeCacheKey(const AlgorithmKey& key,
                                              const std::string& deploy_id) {
    return key.ToUniqueString() + "|" + deploy_id;
}

Result<std::shared_ptr<IAlgorithmRunner>> RuntimeRunnerCache::GetOrLoad(
    const AlgorithmEntry& entry,
    const RuntimeFactory& factory,
    const std::string& deploy_id) {
    const std::string cache_key = MakeCacheKey(entry, deploy_id);
    const std::string fingerprint = BuildFingerprint(entry);
    const bool is_python = entry.key.backend_type == BackendType::kPythonHttpService;

    std::shared_ptr<LoadState> load_state;
    std::uint64_t load_generation = 0;
    {
        std::unique_lock<std::mutex> lock(mutex_);
        auto& cache = is_python ? python_pool_cache_ : onnx_cache_;
        const auto cached = cache.find(cache_key);
        if (cached != cache.end() && cached->second.fingerprint == fingerprint &&
            cached->second.runner) {
            return cached->second.runner;
        }

        const auto loading = loading_.find(cache_key);
        if (loading != loading_.end()) {
            load_state = loading->second;
            load_state->cv.wait(lock, [&load_state]() { return load_state->complete; });

            // 唤醒后重新从缓存读取，而不是复用可能已被 Invalidate 的旧结果。
            const auto rechecked = cache.find(cache_key);
            if (rechecked != cache.end() && rechecked->second.fingerprint == fingerprint &&
                rechecked->second.runner) {
                return rechecked->second.runner;
            }
            if (!load_state->status.ok()) {
                return load_state->status;
            }
            return Status::Error(
                ErrorCode::kServiceUnavailable,
                "Runner load completed but was invalidated before this request could use it.");
        }

        load_state = std::make_shared<LoadState>();
        load_generation = generation_;
        load_state->generation = load_generation;
        loading_[cache_key] = load_state;
    }

    // 真正的初始化在缓存全局锁外执行，允许不同算法并行预热。
    auto runner = factory.Create(entry.key.backend_type);
    Status load_status = Status::Ok();
    if (!runner) {
        load_status = Status::Error(
            ErrorCode::kUnsupportedBackendType,
            "No runtime runner is registered for backend_type=" +
                ToString(entry.key.backend_type) + ".");
    } else {
        load_status = runner->Load(entry);
    }

    std::shared_ptr<IAlgorithmRunner> shared_runner;
    if (load_status.ok()) {
        shared_runner = std::shared_ptr<IAlgorithmRunner>(std::move(runner));
    }

    {
        std::lock_guard<std::mutex> lock(mutex_);
        const bool generation_matches = generation_ == load_generation;
        if (load_status.ok() && generation_matches) {
            auto& cache = is_python ? python_pool_cache_ : onnx_cache_;
            cache[cache_key] = CachedRunner{fingerprint, shared_runner};
        }

        if (!generation_matches && load_status.ok()) {
            load_status = Status::Error(
                ErrorCode::kServiceUnavailable,
                "Runner load was superseded by cache invalidation.");
        }

        load_state->status = load_status;
        load_state->complete = true;
        const auto loading = loading_.find(cache_key);
        if (loading != loading_.end() && loading->second == load_state) {
            loading_.erase(loading);
        }
    }
    load_state->cv.notify_all();

    if (!load_status.ok()) {
        return load_status;
    }
    return shared_runner;
}

bool RuntimeRunnerCache::IsLoaded(const AlgorithmEntry& entry,
                                  const std::string& deploy_id) const {
    return GetCachedRunner(entry, deploy_id) != nullptr;
}

std::shared_ptr<IAlgorithmRunner> RuntimeRunnerCache::GetCachedRunner(
    const AlgorithmEntry& entry, const std::string& deploy_id) const {
    const std::string cache_key = MakeCacheKey(entry, deploy_id);
    const std::string fingerprint = BuildFingerprint(entry);
    std::lock_guard<std::mutex> lock(mutex_);

    const auto& cache = entry.key.backend_type == BackendType::kPythonHttpService
                            ? python_pool_cache_
                            : onnx_cache_;
    const auto it = cache.find(cache_key);
    if (it != cache.end() && it->second.fingerprint == fingerprint && it->second.runner) {
        return it->second.runner;
    }
    return nullptr;
}

void RuntimeRunnerCache::Invalidate(const AlgorithmKey& key) {
    const std::string prefix = key.ToUniqueString() + "|";
    std::lock_guard<std::mutex> lock(mutex_);
    ++generation_;
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

void RuntimeRunnerCache::InvalidateDeployment(const AlgorithmKey& key,
                                               const std::string& deploy_id) {
    const std::string cache_key = MakeCacheKey(key, deploy_id);
    std::lock_guard<std::mutex> lock(mutex_);
    ++generation_;
    onnx_cache_.erase(cache_key);
    python_pool_cache_.erase(cache_key);
}

void RuntimeRunnerCache::Clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    ++generation_;
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
