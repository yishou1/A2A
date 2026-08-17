#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"
#include "algolib/io/tensor_blob.h"
#include "algolib/runtime/algorithm_request.h"

namespace algolib {

struct TensorContractTensor {
    std::string name;
    TensorDataType dtype = TensorDataType::kFloat32;
    std::vector<std::int64_t> shape;
};

struct TensorContract {
    std::vector<TensorContractTensor> inputs;
    std::vector<TensorContractTensor> outputs;
};

struct JsonToTensorMapping {
    std::string json_path = "$";
    std::string tensor_name;
    std::optional<TensorDataType> dtype;
    std::vector<std::int64_t> shape;
    bool has_shape = false;
};

struct RawTensorOutputMapping {
    std::string tensor_name;
    std::string json_path = "$";
};

struct PreprocessConfig {
    std::string type = "no_op";
    bool lowercase = false;
    std::string input_field = "text";
    std::string tensor_name = "input";
    std::vector<JsonToTensorMapping> tensor_mappings;
};

struct PostprocessConfig {
    std::string type = "no_op";
    int top_k = 1;
    std::string tensor_name = "output";
    std::vector<RawTensorOutputMapping> output_mappings;
};

// 中文注释：OnnxPipeline 负责解析 preprocess/postprocess 配置并执行 Phase 4 支持的最小流程。
class OnnxPipeline {
public:
    Result<TensorContract> LoadTensorContract(const std::filesystem::path& config_path) const;
    Result<PreprocessConfig> LoadPreprocessConfig(const std::filesystem::path& config_path) const;
    Result<PostprocessConfig> LoadPostprocessConfig(const std::filesystem::path& config_path) const;

    std::vector<std::string> ExpectedInputTensorNames(
        const PreprocessConfig& config,
        const std::optional<TensorContract>& tensor_contract = std::nullopt) const;
    std::vector<std::string> ExpectedOutputTensorNames(
        const PostprocessConfig& config,
        const std::optional<TensorContract>& tensor_contract = std::nullopt) const;

    Result<std::vector<TensorBlob>> RunPreprocess(const PreprocessConfig& config,
                                                  const AlgorithmRequest& request,
                                                  const std::optional<TensorContract>&
                                                      tensor_contract = std::nullopt) const;
    Result<nlohmann::json> RunPostprocess(
        const PostprocessConfig& config,
        const std::vector<TensorBlob>& output_tensors,
        const std::filesystem::path& label_map_path) const;
};

}  // namespace algolib
