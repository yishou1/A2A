#pragma once

#include <memory>

#include "algolib/core/backend_type.h"
#include "algolib/runtime/algorithm_runner.h"

namespace algolib {

// 中文注释：RuntimeFactory 负责按 backend_type 创建对应 runner，保持统一执行入口解耦。
class RuntimeFactory {
public:
    std::unique_ptr<IAlgorithmRunner> Create(BackendType backend_type) const;
};

}  // namespace algolib
