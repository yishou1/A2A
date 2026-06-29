#pragma once

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"
#include "algolib/runtime/algorithm_runner.h"

namespace algolib {

// 中文注释：GoldenCaseRunner 在注册阶段复用 runner 执行至少一个 golden case。
class GoldenCaseRunner {
public:
    Status Run(const AlgorithmEntry& entry, IAlgorithmRunner* runner) const;
};

}  // namespace algolib
