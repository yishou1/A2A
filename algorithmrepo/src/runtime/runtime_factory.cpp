#include "algolib/runtime/runtime_factory.h"

#include <memory>

#include "algolib/runtime/onnx_runner.h"
#include "algolib/runtime/python_http_runner.h"

namespace algolib {

std::unique_ptr<IAlgorithmRunner> RuntimeFactory::Create(BackendType backend_type) const {
    switch (backend_type) {
        case BackendType::kOnnx:
            return std::make_unique<OnnxRunner>();
        case BackendType::kPythonHttpService:
            return std::make_unique<PythonHttpRunner>();
    }

    return nullptr;
}

}  // namespace algolib
