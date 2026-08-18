#pragma once

#include <cstddef>
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
// ONNX 后端：
//   每个 (AlgorithmKey + deploy_id) 对应一个 OnnxRunner，
//   以 "key|deploy_id" 为 cache_key 存入 onnx_cache_。
//   fingerprint 变化时自动失效重建。
//
// PythonHttpService 后端（并发池）：
//   每个 (AlgorithmKey + deploy_id) 对应一个 PythonHttpRunnerPool 实例，
//   存入 python_pool_cache_。池一旦 Load 完成，后续 GetOrLoad 直接返回池本身
//   作为 shared_ptr<IAlgorithmRunner>（Run 内部做 Checkout/Return）。
//   pool_size 通过 RuntimeFactory 配置。
//
// cache key 格式："{algorithm_id}/{version}/{backend_type}|{deploy_id}"
class RuntimeRunnerCache {
public:
    // 中文注释：deploy_id 可选；若为空则回退到不带 deploy_id 的原始行为。
    Result<std::shared_ptr<IAlgorithmRunner>> GetOrLoad(const AlgorithmEntry& entry,
                                                        const RuntimeFactory& factory,
                                                        const std::string& deploy_id = {});

    // 中文注释：IsLoaded — 检查指定 (entry, deploy_id) 的 runner/pool 是否已在缓存。
    bool IsLoaded(const AlgorithmEntry& entry,
                  const std::string& deploy_id = {}) const;

    // 中文注释：GetCachedRunner — 获取已缓存的 runner（不触发加载）。
    // 对 Python 类型返回 PythonHttpRunnerPool 的 shared_ptr（Run 时内部池化）。
    std::shared_ptr<IAlgorithmRunner> GetCachedRunner(const AlgorithmEntry& entry,
                                                       const std::string& deploy_id = {}) const;

    // 中文注释：Invalidate — 清除指定模型的所有缓存条目（ONNX + Python 均清除）。
    void Invalidate(const AlgorithmKey& key);

    // 中文注释：InvalidateDeployment — 只清除指定 deploy_id 对应的单条缓存。
    void InvalidateDeployment(const AlgorithmKey& key, const std::string& deploy_id);

    void Clear();

    // 中文注释：Size — ONNX 缓存条目数 + Python Pool 缓存条目数之和。
    std::size_t Size() const;

private:
    struct CachedRunner {
        std::string fingerprint;
        std::shared_ptr<IAlgorithmRunner> runner;
    };

    // cache key = AlgorithmKey::ToUniqueString() + "|" + deploy_id
    static std::string MakeCacheKey(const AlgorithmEntry& entry,
                                     const std::string& deploy_id);
    static std::string MakeCacheKey(const AlgorithmKey& key,
                                     const std::string& deploy_id);

    std::string BuildFingerprint(const AlgorithmEntry& entry) const;

    mutable std::mutex mutex_;

    // 中文注释：ONNX runner 缓存（key = AlgorithmKey + deploy_id）。
    std::map<std::string, CachedRunner> onnx_cache_;

    // 中文注释：Python HTTP Service 连接池缓存（key = AlgorithmKey + deploy_id）。
    // 值为 PythonHttpRunnerPool（通过 IAlgorithmRunner 接口持有）。
    std::map<std::string, CachedRunner> python_pool_cache_;
};

}  // namespace algolib
