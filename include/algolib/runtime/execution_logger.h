#pragma once

#include <cstdint>
#include <filesystem>

#include "algolib/core/status.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"

namespace algolib {

// 中文注释：ExecutionLogger 负责把统一 run 接口的审计记录追加到本地 JSONL 文件。
class ExecutionLogger {
public:
    explicit ExecutionLogger(std::filesystem::path log_path);

    Status Append(const AlgorithmRequest& request,
                  const AlgorithmResult& result,
                  std::int64_t latency_ms) const;

    const std::filesystem::path& log_path() const;

private:
    std::filesystem::path log_path_;
};

}  // namespace algolib
