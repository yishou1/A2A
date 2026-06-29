#pragma once

#include <filesystem>

#include "algolib/registry/algorithm_registry.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"
#include "algolib/runtime/execution_logger.h"
#include "algolib/runtime/runtime_factory.h"

namespace algolib {

// 中文注释：ExecutionCoordinator 把 registry、schema 校验、runner 创建和审计日志串成统一 run 流程。
class ExecutionCoordinator {
public:
    explicit ExecutionCoordinator(const AlgorithmRegistry& registry,
                                  std::filesystem::path execution_log_path = {});

    AlgorithmResult Run(const AlgorithmRequest& request);
    const std::filesystem::path& execution_log_path() const;

private:
    const AlgorithmRegistry& registry_;
    RuntimeFactory runtime_factory_;
    ExecutionLogger execution_logger_;
};

}  // namespace algolib
