#pragma once

#include "algolib/core/algorithm_entry.h"
#include "algolib/io/http_client.h"
#include "algolib/runtime/algorithm_runner.h"

namespace algolib {

// 中文注释：PythonHttpRunner 把 Phase 3 的服务契约接入统一执行接口。
class PythonHttpRunner : public IAlgorithmRunner {
public:
    Status Load(const AlgorithmEntry& entry) override;
    AlgorithmResult Run(const AlgorithmRequest& request) override;
    HealthStatus HealthCheck() const override;

private:
    HttpClient http_client_;
    AlgorithmEntry entry_;
    bool loaded_ = false;
};

}  // namespace algolib
