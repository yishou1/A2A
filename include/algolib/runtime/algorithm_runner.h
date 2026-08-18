#pragma once

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"
#include "algolib/runtime/health_status.h"

namespace algolib {

class IAlgorithmRunner {
public:
    virtual ~IAlgorithmRunner() = default;

    // 中文注释：Load — 将模型文件加载进内存/显存并完成 session 初始化。
    virtual Status Load(const AlgorithmEntry& entry) = 0;

    // 中文注释：Unload — 释放已加载的模型资源（session、内存、显存）。
    //           默认实现为空操作，子类按需覆盖。
    virtual Status Unload() { return Status::Ok(); }

    virtual AlgorithmResult Run(const AlgorithmRequest& request) = 0;
    virtual HealthStatus HealthCheck() const = 0;
};

}  // namespace algolib
