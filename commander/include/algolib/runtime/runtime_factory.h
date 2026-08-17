#pragma once

#include <cstddef>
#include <memory>

#include "algolib/core/backend_type.h"
#include "algolib/runtime/algorithm_runner.h"

namespace algolib {

// 中文注释：RuntimeFactory 负责按 backend_type 创建对应 runner，保持统一执行入口解耦。
//
// 对于 python_http_service 后端，可通过 python_pool_size / python_checkout_timeout_ms
// 控制连接池容量。pool_size=1 等同于无并发（退化为单连接行为）。
// 对于 onnx 后端，pool_size 参数被忽略（每个 (entry, deploy_id) 仅一个 OnnxRunner）。
class RuntimeFactory {
public:
    // 中文注释：python_pool_size — Python HTTP Service 连接池容量（默认 4）。
    static constexpr std::size_t kDefaultPythonPoolSize = 4;

    explicit RuntimeFactory(std::size_t python_pool_size = kDefaultPythonPoolSize,
                            int python_checkout_timeout_ms = 5000)
        : python_pool_size_(python_pool_size > 0 ? python_pool_size : kDefaultPythonPoolSize),
          python_checkout_timeout_ms_(python_checkout_timeout_ms > 0
                                          ? python_checkout_timeout_ms
                                          : 5000) {}

    // 中文注释：Create — 按 backend_type 创建 runner。
    // kOnnx              → OnnxRunner（无池化）
    // kPythonHttpService → PythonHttpRunnerPool（pool_size = python_pool_size_）
    std::unique_ptr<IAlgorithmRunner> Create(BackendType backend_type) const;

    std::size_t python_pool_size() const { return python_pool_size_; }

private:
    std::size_t python_pool_size_;
    int         python_checkout_timeout_ms_;
};

}  // namespace algolib
