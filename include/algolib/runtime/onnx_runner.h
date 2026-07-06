#pragma once

#include <filesystem>
#include <optional>

#include "algolib/runtime/algorithm_runner.h"
#include "algolib/runtime/onnx_pipeline.h"
#include "algolib/runtime/onnx_session.h"

namespace algolib {

// 中文注释：OnnxRunner 封装 Phase 4 的最小 ONNX 运行链路，可在 stub 与真实 ORT 间切换。
class OnnxRunner : public IAlgorithmRunner {
public:
    Status Load(const AlgorithmEntry& entry) override;
    AlgorithmResult Run(const AlgorithmRequest& request) override;
    HealthStatus HealthCheck() const override;

private:
    AlgorithmResult BuildErrorResult(const AlgorithmRequest& request,
                                     const Status& status) const;

    AlgorithmEntry entry_;
    OnnxPipeline pipeline_;
    OnnxSessionWrapper session_;
    std::optional<PreprocessConfig> preprocess_config_;
    std::optional<PostprocessConfig> postprocess_config_;
    std::optional<TensorContract> tensor_contract_;
    bool loaded_ = false;
};

}  // namespace algolib
