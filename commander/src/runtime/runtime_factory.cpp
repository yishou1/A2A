#include "algolib/runtime/runtime_factory.h"

#include <memory>

#include "algolib/runtime/onnx_runner.h"
#include "algolib/runtime/python_http_runner_pool.h"

namespace algolib {

std::unique_ptr<IAlgorithmRunner> RuntimeFactory::Create(BackendType backend_type) const {
    switch (backend_type) {
        case BackendType::kOnnx:
            return std::make_unique<OnnxRunner>();
        case BackendType::kPythonHttpService:
            // 中文注释：Python 后端统一通过 PythonHttpRunnerPool 包装，
            // 即使 pool_size=1 也经过池的 Checkout/Return 流程（保证接口一致性）。
            return std::make_unique<PythonHttpRunnerPool>(
                python_pool_size_, python_checkout_timeout_ms_);
    }

    return nullptr;
}

}  // namespace algolib
