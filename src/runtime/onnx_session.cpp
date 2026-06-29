#include "algolib/runtime/onnx_session.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <numeric>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#if ALGOLIB_WITH_ONNXRUNTIME
#include <onnxruntime_cxx_api.h>
#endif

namespace algolib {
namespace {

using nlohmann::json;

std::string ToLowerAscii(std::string value) {
    for (char& ch : value) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return value;
}

std::string ExtractSourceText(const std::vector<TensorBlob>& input_tensors) {
    for (const auto& tensor : input_tensors) {
        if (tensor.metadata.contains("source_text") && tensor.metadata.at("source_text").is_string()) {
            return tensor.metadata.at("source_text").get<std::string>();
        }
        if (tensor.metadata.contains("source_json")) {
            const auto& source_json = tensor.metadata.at("source_json");
            if (source_json.is_object() && source_json.contains("text") &&
                source_json.at("text").is_string()) {
                return source_json.at("text").get<std::string>();
            }
        }
    }
    return "";
}

double ExtractNumericSignal(const std::vector<TensorBlob>& input_tensors) {
    for (const auto& tensor : input_tensors) {
        if (tensor.values.is_number()) {
            return tensor.values.get<double>();
        }
        if (tensor.values.is_array() && !tensor.values.empty()) {
            const auto& first = tensor.values.front();
            if (first.is_number()) {
                return first.get<double>();
            }
            if (first.is_array() && !first.empty() && first.front().is_number()) {
                return first.front().get<double>();
            }
        }
    }
    return 0.0;
}

std::vector<double> BuildStubClassificationScores(const std::string& source_text,
                                                  double numeric_signal) {
    const std::string lowered = ToLowerAscii(source_text);
    if (lowered.find("task") != std::string::npos) {
        return {0.96, 0.02, 0.02};
    }
    if (lowered.find("report") != std::string::npos) {
        return {0.03, 0.93, 0.04};
    }
    if (numeric_signal > 0.5) {
        return {0.2, 0.3, 0.5};
    }
    return {0.08, 0.12, 0.80};
}

std::size_t ComputeElementCount(const std::vector<std::int64_t>& shape) {
    if (shape.empty()) {
        return 1;
    }

    std::size_t element_count = 1;
    for (std::int64_t dimension : shape) {
        if (dimension < 0) {
            return 0;
        }
        element_count *= static_cast<std::size_t>(dimension);
    }
    return element_count;
}

Status ValidateTensorShape(const TensorBlob& tensor) {
    const std::size_t expected_count = ComputeElementCount(tensor.shape);
    if (expected_count == 0 && !tensor.shape.empty()) {
        return Status::Error(
            ErrorCode::kOnnxInputTensorMismatch,
            "Input tensor contains unsupported negative shape dimension: " + tensor.name + ".");
    }
    return Status::Ok();
}

Status FlattenFloatValues(const json& value,
                          std::vector<float>* flat_values,
                          const std::string& tensor_name) {
    if (value.is_array()) {
        for (const auto& item : value) {
            auto status = FlattenFloatValues(item, flat_values, tensor_name);
            if (!status.ok()) {
                return status;
            }
        }
        return Status::Ok();
    }

    if (value.is_boolean()) {
        flat_values->push_back(value.get<bool>() ? 1.0F : 0.0F);
        return Status::Ok();
    }

    if (!value.is_number()) {
        return Status::Error(
            ErrorCode::kOnnxInputTensorMismatch,
            "Input tensor `" + tensor_name + "` contains a non-numeric value.");
    }

    flat_values->push_back(static_cast<float>(value.get<double>()));
    return Status::Ok();
}

Status FlattenInt64Values(const json& value,
                          std::vector<std::int64_t>* flat_values,
                          const std::string& tensor_name) {
    if (value.is_array()) {
        for (const auto& item : value) {
            auto status = FlattenInt64Values(item, flat_values, tensor_name);
            if (!status.ok()) {
                return status;
            }
        }
        return Status::Ok();
    }

    if (value.is_boolean()) {
        flat_values->push_back(value.get<bool>() ? 1 : 0);
        return Status::Ok();
    }

    if (!value.is_number_integer()) {
        return Status::Error(
            ErrorCode::kOnnxInputTensorMismatch,
            "Input tensor `" + tensor_name + "` expects int64 values.");
    }

    flat_values->push_back(value.get<std::int64_t>());
    return Status::Ok();
}

Status FlattenStringValues(const json& value,
                           std::vector<std::string>* flat_values,
                           const std::string& tensor_name) {
    if (value.is_array()) {
        for (const auto& item : value) {
            auto status = FlattenStringValues(item, flat_values, tensor_name);
            if (!status.ok()) {
                return status;
            }
        }
        return Status::Ok();
    }

    if (!value.is_string()) {
        return Status::Error(
            ErrorCode::kOnnxInputTensorMismatch,
            "Input tensor `" + tensor_name + "` expects string values.");
    }

    flat_values->push_back(value.get<std::string>());
    return Status::Ok();
}

template <typename ValueType, typename Converter>
json BuildJsonFromFlatValuesRecursive(const std::vector<ValueType>& flat_values,
                                      const std::vector<std::int64_t>& shape,
                                      std::size_t dimension_index,
                                      std::size_t* flat_index,
                                      Converter converter) {
    if (shape.empty()) {
        return converter(flat_values.at((*flat_index)++));
    }

    const std::int64_t current_dimension = shape.at(dimension_index);
    json array = json::array();
    if (dimension_index + 1 == shape.size()) {
        for (std::int64_t index = 0; index < current_dimension; ++index) {
            array.push_back(converter(flat_values.at((*flat_index)++)));
        }
        return array;
    }

    for (std::int64_t index = 0; index < current_dimension; ++index) {
        array.push_back(BuildJsonFromFlatValuesRecursive(flat_values,
                                                         shape,
                                                         dimension_index + 1,
                                                         flat_index,
                                                         converter));
    }
    return array;
}

template <typename ValueType, typename Converter>
json BuildJsonFromFlatValues(const std::vector<ValueType>& flat_values,
                             const std::vector<std::int64_t>& shape,
                             Converter converter) {
    std::size_t flat_index = 0;
    return BuildJsonFromFlatValuesRecursive(flat_values, shape, 0, &flat_index, converter);
}

#if ALGOLIB_WITH_ONNXRUNTIME

Ort::Env& GetOrtEnv() {
    // 中文注释：进程级共享 ORT Env，避免每次加载模型都重复初始化运行时。
    static Ort::Env env(ORT_LOGGING_LEVEL_WARNING, "algolib");
    return env;
}

std::vector<std::string> ReadTensorNames(const Ort::Session& session, bool read_inputs) {
    Ort::AllocatorWithDefaultOptions allocator;
    const std::size_t tensor_count = read_inputs ? session.GetInputCount() : session.GetOutputCount();

    std::vector<std::string> names;
    names.reserve(tensor_count);
    for (std::size_t index = 0; index < tensor_count; ++index) {
        auto allocated_name = read_inputs
                                  ? session.GetInputNameAllocated(index, allocator)
                                  : session.GetOutputNameAllocated(index, allocator);
        names.emplace_back(allocated_name.get());
    }
    return names;
}

struct OrtInputTensor {
    Ort::Value value{nullptr};
    std::vector<float> float_values;
    std::vector<std::int64_t> int64_values;
    std::vector<std::string> string_values;
    std::vector<const char*> string_views;
};

Result<OrtInputTensor> BuildOrtInputTensor(const TensorBlob& tensor,
                                           const Ort::MemoryInfo& memory_info,
                                           OrtAllocator* allocator) {
    auto shape_status = ValidateTensorShape(tensor);
    if (!shape_status.ok()) {
        return shape_status;
    }

    const std::size_t expected_count = ComputeElementCount(tensor.shape);
    OrtInputTensor ort_tensor;

    switch (tensor.dtype) {
        case TensorDataType::kFloat32: {
            auto flatten_status = FlattenFloatValues(tensor.values, &ort_tensor.float_values, tensor.name);
            if (!flatten_status.ok()) {
                return flatten_status;
            }
            if (ort_tensor.float_values.size() != expected_count) {
                return Status::Error(
                    ErrorCode::kOnnxInputTensorMismatch,
                    "Input tensor `" + tensor.name + "` shape does not match its value count.");
            }
            ort_tensor.value = Ort::Value::CreateTensor<float>(
                memory_info,
                ort_tensor.float_values.data(),
                ort_tensor.float_values.size(),
                tensor.shape.data(),
                tensor.shape.size());
            return ort_tensor;
        }
        case TensorDataType::kInt64: {
            auto flatten_status = FlattenInt64Values(tensor.values, &ort_tensor.int64_values, tensor.name);
            if (!flatten_status.ok()) {
                return flatten_status;
            }
            if (ort_tensor.int64_values.size() != expected_count) {
                return Status::Error(
                    ErrorCode::kOnnxInputTensorMismatch,
                    "Input tensor `" + tensor.name + "` shape does not match its value count.");
            }
            ort_tensor.value = Ort::Value::CreateTensor<std::int64_t>(
                memory_info,
                ort_tensor.int64_values.data(),
                ort_tensor.int64_values.size(),
                tensor.shape.data(),
                tensor.shape.size());
            return ort_tensor;
        }
        case TensorDataType::kString: {
            auto flatten_status = FlattenStringValues(tensor.values, &ort_tensor.string_values, tensor.name);
            if (!flatten_status.ok()) {
                return flatten_status;
            }
            if (ort_tensor.string_values.size() != expected_count) {
                return Status::Error(
                    ErrorCode::kOnnxInputTensorMismatch,
                    "Input tensor `" + tensor.name + "` shape does not match its value count.");
            }

            ort_tensor.value = Ort::Value::CreateTensor(
                allocator,
                tensor.shape.data(),
                tensor.shape.size(),
                ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING);
            ort_tensor.string_views.reserve(ort_tensor.string_values.size());
            for (const auto& item : ort_tensor.string_values) {
                ort_tensor.string_views.push_back(item.c_str());
            }
            ort_tensor.value.FillStringTensor(ort_tensor.string_views.data(),
                                              ort_tensor.string_views.size());
            return ort_tensor;
        }
    }

    return Status::Error(ErrorCode::kOnnxInputTensorMismatch,
                         "Unsupported ONNX input tensor dtype.");
}

Result<TensorBlob> ConvertOrtOutputTensor(const std::string& tensor_name,
                                          const Ort::Value& ort_value,
                                          const std::string& execution_provider) {
    if (!ort_value.IsTensor()) {
        return Status::Error(ErrorCode::kOnnxRuntimeError,
                             "ONNX Runtime returned a non-tensor output: " + tensor_name + ".");
    }

    const auto tensor_info = ort_value.GetTensorTypeAndShapeInfo();
    const auto ort_shape = tensor_info.GetShape();
    const auto element_type = tensor_info.GetElementType();
    const std::size_t element_count = tensor_info.GetElementCount();

    TensorBlob tensor;
    tensor.name = tensor_name;
    tensor.shape.assign(ort_shape.begin(), ort_shape.end());
    tensor.metadata = {
        {"session_backend", "onnxruntime"},
        {"execution_provider", execution_provider},
    };

    switch (element_type) {
        case ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT: {
            const float* raw_values = ort_value.GetTensorData<float>();
            std::vector<float> flat_values(raw_values, raw_values + element_count);
            tensor.dtype = TensorDataType::kFloat32;
            tensor.values = BuildJsonFromFlatValues(
                flat_values, tensor.shape, [](float value) { return json(value); });
            return tensor;
        }
        case ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64: {
            const std::int64_t* raw_values = ort_value.GetTensorData<std::int64_t>();
            std::vector<std::int64_t> flat_values(raw_values, raw_values + element_count);
            tensor.dtype = TensorDataType::kInt64;
            tensor.values = BuildJsonFromFlatValues(
                flat_values, tensor.shape, [](std::int64_t value) { return json(value); });
            return tensor;
        }
        case ONNX_TENSOR_ELEMENT_DATA_TYPE_STRING: {
            std::vector<std::string> flat_values;
            flat_values.reserve(element_count);
            for (std::size_t index = 0; index < element_count; ++index) {
                flat_values.push_back(ort_value.GetStringTensorElement(index));
            }
            tensor.dtype = TensorDataType::kString;
            tensor.values = BuildJsonFromFlatValues(
                flat_values, tensor.shape, [](const std::string& value) { return json(value); });
            return tensor;
        }
        default:
            return Status::Error(
                ErrorCode::kOnnxRuntimeError,
                "ONNX Runtime output tensor `" + tensor_name +
                    "` uses an unsupported element type.");
    }
}

#endif  // ALGOLIB_WITH_ONNXRUNTIME

}  // namespace

struct OnnxSessionWrapper::Impl {
#if ALGOLIB_WITH_ONNXRUNTIME
    Impl() : memory_info(Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault)) {}

    Ort::AllocatorWithDefaultOptions allocator;
    Ort::MemoryInfo memory_info;
    Ort::Session session{nullptr};
#endif
};

OnnxSessionWrapper::OnnxSessionWrapper() = default;
OnnxSessionWrapper::~OnnxSessionWrapper() = default;
OnnxSessionWrapper::OnnxSessionWrapper(OnnxSessionWrapper&& other) noexcept = default;
OnnxSessionWrapper& OnnxSessionWrapper::operator=(OnnxSessionWrapper&& other) noexcept = default;

Status OnnxSessionWrapper::LoadModel(const std::filesystem::path& model_path,
                                     const std::string& execution_provider,
                                     const std::vector<std::string>& expected_input_names,
                                     const std::vector<std::string>& expected_output_names) {
    model_path_.clear();
    execution_provider_.clear();
    input_names_.clear();
    output_names_.clear();
    loaded_ = false;
    impl_.reset();

    if (!std::filesystem::exists(model_path)) {
        return Status::Error(ErrorCode::kOnnxModelNotFound,
                             "ONNX model file was not found: " + model_path.generic_string());
    }

    if (!execution_provider.empty() && execution_provider != "cpu") {
        return Status::Error(ErrorCode::kOnnxLoadFailed,
                             "The current build only supports execution_provider=cpu.");
    }

#if ALGOLIB_WITH_ONNXRUNTIME
    try {
        auto session_impl = std::make_unique<Impl>();
        Ort::SessionOptions session_options;
        session_options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_EXTENDED);

#if defined(_WIN32)
        const std::wstring model_path_utf16 = model_path.wstring();
        session_impl->session =
            Ort::Session(GetOrtEnv(), model_path_utf16.c_str(), session_options);
#else
        const std::string model_path_utf8 = model_path.string();
        session_impl->session =
            Ort::Session(GetOrtEnv(), model_path_utf8.c_str(), session_options);
#endif

        model_path_ = model_path;
        execution_provider_ = execution_provider.empty() ? "cpu" : execution_provider;
        input_names_ = ReadTensorNames(session_impl->session, true);
        output_names_ = ReadTensorNames(session_impl->session, false);
        loaded_ = true;
        impl_ = std::move(session_impl);
        return Status::Ok();
    } catch (const Ort::Exception& ex) {
        model_path_.clear();
        execution_provider_.clear();
        input_names_.clear();
        output_names_.clear();
        loaded_ = false;
        impl_.reset();
        return Status::Error(ErrorCode::kOnnxLoadFailed,
                             "Failed to load ONNX model with ONNX Runtime: " +
                                 std::string(ex.what()));
    }
#else
    model_path_ = model_path;
    execution_provider_ = execution_provider.empty() ? "cpu" : execution_provider;
    input_names_ = expected_input_names;
    output_names_ = expected_output_names;
    loaded_ = true;
    return Status::Ok();
#endif
}

Result<std::vector<TensorBlob>> OnnxSessionWrapper::Run(
    const std::vector<TensorBlob>& input_tensors) const {
    if (!loaded_) {
        return Status::Error(ErrorCode::kOnnxRuntimeError,
                             "ONNX session is not loaded.");
    }
    if (input_tensors.empty()) {
        return Status::Error(ErrorCode::kOnnxInputTensorMismatch,
                             "ONNX session requires at least one input tensor.");
    }

#if ALGOLIB_WITH_ONNXRUNTIME
    if (!impl_) {
        return Status::Error(ErrorCode::kOnnxRuntimeError,
                             "ONNX Runtime session implementation is not available.");
    }

    try {
        std::unordered_map<std::string, const TensorBlob*> input_lookup;
        input_lookup.reserve(input_tensors.size());
        for (const auto& tensor : input_tensors) {
            input_lookup[tensor.name] = &tensor;
        }

        std::vector<OrtInputTensor> ort_inputs;
        ort_inputs.reserve(input_names_.size());
        std::vector<const char*> ort_input_names;
        ort_input_names.reserve(input_names_.size());

        for (const auto& input_name : input_names_) {
            auto it = input_lookup.find(input_name);
            if (it == input_lookup.end()) {
                return Status::Error(
                    ErrorCode::kOnnxInputTensorMismatch,
                    "Required ONNX input tensor was not provided: " + input_name + ".");
            }

            auto ort_input_result =
                BuildOrtInputTensor(*it->second, impl_->memory_info, impl_->allocator);
            if (!ort_input_result.ok()) {
                return ort_input_result.status();
            }
            ort_inputs.push_back(std::move(ort_input_result.value()));
            ort_input_names.push_back(input_name.c_str());
        }

        std::vector<Ort::Value> ort_input_values;
        ort_input_values.reserve(ort_inputs.size());
        for (auto& ort_input : ort_inputs) {
            ort_input_values.push_back(std::move(ort_input.value));
        }

        std::vector<const char*> ort_output_names;
        ort_output_names.reserve(output_names_.size());
        for (const auto& output_name : output_names_) {
            ort_output_names.push_back(output_name.c_str());
        }

        Ort::RunOptions run_options;
        auto ort_outputs = impl_->session.Run(run_options,
                                              ort_input_names.data(),
                                              ort_input_values.data(),
                                              ort_input_values.size(),
                                              ort_output_names.data(),
                                              ort_output_names.size());

        if (ort_outputs.size() != output_names_.size()) {
            return Status::Error(ErrorCode::kOnnxOutputTensorMismatch,
                                 "ONNX Runtime returned an unexpected number of outputs.");
        }

        std::vector<TensorBlob> output_tensors;
        output_tensors.reserve(ort_outputs.size());
        for (std::size_t index = 0; index < ort_outputs.size(); ++index) {
            auto output_tensor_result =
                ConvertOrtOutputTensor(output_names_.at(index), ort_outputs.at(index), execution_provider_);
            if (!output_tensor_result.ok()) {
                return output_tensor_result.status();
            }
            output_tensors.push_back(std::move(output_tensor_result.value()));
        }
        return output_tensors;
    } catch (const Ort::Exception& ex) {
        return Status::Error(ErrorCode::kOnnxRuntimeError,
                             "ONNX Runtime inference failed: " + std::string(ex.what()));
    }
#else
    const std::string output_name = output_names_.empty() ? "output" : output_names_.front();
    TensorBlob output_tensor;
    output_tensor.name = output_name;
    output_tensor.metadata = {
        {"session_backend", "stub"},
        {"execution_provider", execution_provider_},
    };

    if (output_name == "logits") {
        const std::string source_text = ExtractSourceText(input_tensors);
        const double numeric_signal = ExtractNumericSignal(input_tensors);
        const std::vector<double> scores =
            BuildStubClassificationScores(source_text, numeric_signal);

        output_tensor.dtype = TensorDataType::kFloat32;
        output_tensor.shape = {1, static_cast<std::int64_t>(scores.size())};
        output_tensor.values = json::array({scores});
        return std::vector<TensorBlob>{output_tensor};
    }

    output_tensor.dtype = input_tensors.front().dtype;
    output_tensor.shape = input_tensors.front().shape;
    output_tensor.values = input_tensors.front().values;
    output_tensor.metadata["source_tensor_name"] = input_tensors.front().name;
    return std::vector<TensorBlob>{output_tensor};
#endif
}

const std::vector<std::string>& OnnxSessionWrapper::input_names() const {
    return input_names_;
}

const std::vector<std::string>& OnnxSessionWrapper::output_names() const {
    return output_names_;
}

bool OnnxSessionWrapper::loaded() const {
    return loaded_;
}

}  // namespace algolib
