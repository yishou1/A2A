#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"
#include "algolib/io/tensor_blob.h"
#include "algolib/runtime/algorithm_request.h"

namespace algolib {

struct PreprocessConfig {
    std::string type = "no_op";
    bool lowercase = false;
    std::string input_field = "text";
    std::string tensor_name = "input";
};

struct PostprocessConfig {
    std::string type = "no_op";
    int top_k = 1;
    std::string tensor_name = "output";
};

// 中文注释：OnnxPipeline 负责解析 preprocess/postprocess 配置并执行 Phase 4 支持的最小流程。
class OnnxPipeline {
public:
    Result<PreprocessConfig> LoadPreprocessConfig(const std::filesystem::path& config_path) const;
    Result<PostprocessConfig> LoadPostprocessConfig(const std::filesystem::path& config_path) const;

    std::vector<std::string> ExpectedInputTensorNames(const PreprocessConfig& config) const;
    std::vector<std::string> ExpectedOutputTensorNames(const PostprocessConfig& config) const;

    Result<std::vector<TensorBlob>> RunPreprocess(const PreprocessConfig& config,
                                                  const AlgorithmRequest& request) const;
    Result<nlohmann::json> RunPostprocess(
        const PostprocessConfig& config,
        const std::vector<TensorBlob>& output_tensors,
        const std::filesystem::path& label_map_path) const;
};

}  // namespace algolib
