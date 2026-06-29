#pragma once

#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include "algolib/core/status.h"
#include "algolib/io/tensor_blob.h"

namespace algolib {

// 中文注释：OnnxSessionWrapper 统一封装 stub 与真实 ONNX Runtime 两种会话实现。
class OnnxSessionWrapper {
public:
    OnnxSessionWrapper();
    ~OnnxSessionWrapper();

    OnnxSessionWrapper(OnnxSessionWrapper&& other) noexcept;
    OnnxSessionWrapper& operator=(OnnxSessionWrapper&& other) noexcept;

    OnnxSessionWrapper(const OnnxSessionWrapper&) = delete;
    OnnxSessionWrapper& operator=(const OnnxSessionWrapper&) = delete;

    Status LoadModel(const std::filesystem::path& model_path,
                     const std::string& execution_provider,
                     const std::vector<std::string>& expected_input_names,
                     const std::vector<std::string>& expected_output_names);

    Result<std::vector<TensorBlob>> Run(const std::vector<TensorBlob>& input_tensors) const;

    const std::vector<std::string>& input_names() const;
    const std::vector<std::string>& output_names() const;
    bool loaded() const;

private:
    std::filesystem::path model_path_;
    std::string execution_provider_;
    std::vector<std::string> input_names_;
    std::vector<std::string> output_names_;
    bool loaded_ = false;
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace algolib
