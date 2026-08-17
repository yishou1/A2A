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

// 中文注释：HTTP Server 复用该缓存，让 ONNX runner/session 可在多次 /run 请求间常驻内存。
class RuntimeRunnerCache {
public:
    Result<std::shared_ptr<IAlgorithmRunner>> GetOrLoad(const AlgorithmEntry& entry,
                                                        const RuntimeFactory& factory);

    void Invalidate(const AlgorithmKey& key);
    void Clear();
    std::size_t Size() const;

private:
    struct CachedRunner {
        std::string fingerprint;
        std::shared_ptr<IAlgorithmRunner> runner;
    };

    std::string BuildFingerprint(const AlgorithmEntry& entry) const;

    mutable std::mutex mutex_;
    std::map<AlgorithmKey, CachedRunner> onnx_cache_;
};

}  // namespace algolib
