#include "algolib/runtime/onnx_runner.h"

#include <filesystem>

#include "algolib/core/error_code.h"
#include "algolib/io/file_utils.h"

namespace algolib {

Status OnnxRunner::Load(const AlgorithmEntry& entry) {
    entry_ = entry;

    const auto preprocess_path = FileUtils::ResolveReference(
        entry.package_root, entry.card.machine_spec.preprocess->config_uri);
    const auto postprocess_path = FileUtils::ResolveReference(
        entry.package_root, entry.card.machine_spec.postprocess->config_uri);
    const auto model_path = FileUtils::ResolveReference(
        entry.package_root, entry.card.machine_spec.runtime.model_uri);

    auto preprocess_result = pipeline_.LoadPreprocessConfig(preprocess_path);
    if (!preprocess_result.ok()) {
        return preprocess_result.status();
    }
    auto postprocess_result = pipeline_.LoadPostprocessConfig(postprocess_path);
    if (!postprocess_result.ok()) {
        return postprocess_result.status();
    }

    const auto expected_input_names =
        pipeline_.ExpectedInputTensorNames(preprocess_result.value());
    const auto expected_output_names =
        pipeline_.ExpectedOutputTensorNames(postprocess_result.value());

    auto load_status = session_.LoadModel(model_path,
                                          entry.card.machine_spec.runtime.execution_provider,
                                          expected_input_names,
                                          expected_output_names);
    if (!load_status.ok()) {
        return load_status;
    }

    if (session_.input_names() != expected_input_names) {
        return Status::Error(ErrorCode::kOnnxInputTensorMismatch,
                             "ONNX model input tensor names do not match the preprocess contract.");
    }
    if (session_.output_names() != expected_output_names) {
        return Status::Error(
            ErrorCode::kOnnxOutputTensorMismatch,
            "ONNX model output tensor names do not match the postprocess contract.");
    }

    preprocess_config_ = preprocess_result.value();
    postprocess_config_ = postprocess_result.value();
    loaded_ = true;
    return Status::Ok();
}

AlgorithmResult OnnxRunner::Run(const AlgorithmRequest& request) {
    if (!loaded_ || !preprocess_config_.has_value() || !postprocess_config_.has_value()) {
        return BuildErrorResult(
            request,
            Status::Error(ErrorCode::kOnnxRuntimeError,
                          "ONNX runner must be loaded before Run()."));
    }

    auto preprocess_result = pipeline_.RunPreprocess(*preprocess_config_, request);
    if (!preprocess_result.ok()) {
        return BuildErrorResult(request, preprocess_result.status());
    }

    auto output_tensors_result = session_.Run(preprocess_result.value());
    if (!output_tensors_result.ok()) {
        return BuildErrorResult(request, output_tensors_result.status());
    }

    const std::filesystem::path label_map_path = entry_.card.machine_spec.postprocess->label_map_uri.empty()
                                                     ? std::filesystem::path()
                                                     : FileUtils::ResolveReference(
                                                           entry_.package_root,
                                                           entry_.card.machine_spec.postprocess
                                                               ->label_map_uri);
    auto outputs_result = pipeline_.RunPostprocess(
        *postprocess_config_, output_tensors_result.value(), label_map_path);
    if (!outputs_result.ok()) {
        return BuildErrorResult(request, outputs_result.status());
    }

    AlgorithmResult result;
    result.ok = true;
    result.request_id = request.request_id;
    result.trace_id = request.trace_id;
    result.algorithm_id = request.algorithm_id;
    result.version = request.version;
    result.backend_type = request.backend_type;
    result.outputs = outputs_result.value();
    result.usage = {
        {"session_backend", ALGOLIB_WITH_ONNXRUNTIME ? "onnxruntime" : "stub"},
        {"execution_provider", entry_.card.machine_spec.runtime.execution_provider.empty()
                                   ? "cpu"
                                   : entry_.card.machine_spec.runtime.execution_provider},
    };
    return result;
}

HealthStatus OnnxRunner::HealthCheck() const {
    if (loaded_ && session_.loaded()) {
        return HealthStatus{true, "ready", "ONNX runner is loaded."};
    }
    return HealthStatus{false, "error", "ONNX runner is not loaded."};
}

AlgorithmResult OnnxRunner::BuildErrorResult(const AlgorithmRequest& request,
                                             const Status& status) const {
    AlgorithmResult result;
    result.ok = false;
    result.request_id = request.request_id;
    result.trace_id = request.trace_id;
    result.algorithm_id = request.algorithm_id;
    result.version = request.version;
    result.backend_type = request.backend_type;
    result.outputs = nlohmann::json::object();
    result.usage = nlohmann::json::object();
    result.error = AlgorithmError{ToString(status.code()), status.message()};
    return result;
}

}  // namespace algolib
