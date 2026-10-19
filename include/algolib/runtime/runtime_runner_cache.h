#pragma once

#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>

#include "algolib/core/algorithm_entry.h"
#include "algolib/runtime/algorithm_runner.h"
#include "algolib/runtime/runtime_factory.h"

namespace algolib {

// 中文注释：RuntimeRunnerCache — 统一 runner 缓存，支持两种后端的池化策略。
//
// 并发语义：
//   - 全局 mutex 仅保护索引和加载状态，不覆盖 runner->Load()。
//   - 同一 (AlgorithmKey, deploy_id) 的首次加载采用 single-flight；不同模型可并行加载。
//   - Invalidate/Clear 通过 generation 让进行中的旧加载不再回写缓存。
//   - 返回 shared_ptr，缓存条目删除后在途请求仍可安全持有 runner。
class RuntimeRunnerCache {
public:
    Result<std::shared_ptr<IAlgorithmRunner>> GetOrLoad(const AlgorithmEntry& entry,
                                                        const RuntimeFactory& factory,
                                                        const std::string& deploy_id = {});

    bool IsLoaded(const AlgorithmEntry& entry,
                  const std::string& deploy_id = {}) const;

    std::shared_ptr<IAlgorithmRunner> GetCachedRunner(const AlgorithmEntry& entry,
                                                       const std::string& deploy_id = {}) const;

    void Invalidate(const AlgorithmKey& key);
    void InvalidateDeployment(const AlgorithmKey& key, const std::string& deploy_id);
    void Clear();
    std::size_t Size() const;

private:
    struct CachedRunner {
        std::string fingerprint;
        std::shared_ptr<IAlgorithmRunner> runner;
    };

    struct LoadState {
        std::condition_variable cv;
        bool complete = false;
        Status status = Status::Ok();
        std::uint64_t generation = 0;
    };

    static std::string MakeCacheKey(const AlgorithmEntry& entry,
                                    const std::string& deploy_id);
    static std::string MakeCacheKey(const AlgorithmKey& key,
                                    const std::string& deploy_id);

    std::string BuildFingerprint(const AlgorithmEntry& entry) const;

    mutable std::mutex mutex_;
    std::uint64_t generation_ = 0;

    std::map<std::string, CachedRunner> onnx_cache_;
    std::map<std::string, CachedRunner> python_pool_cache_;
    std::map<std::string, std::shared_ptr<LoadState>> loading_;
};

}  // namespace algolib
