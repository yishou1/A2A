#include "algolib/runtime/onnx_pipeline.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <numeric>
#include <string>
#include <utility>
#include <vector>

#include <yaml-cpp/yaml.h>

#include "algolib/io/json_utils.h"
#include "algolib/io/yaml_utils.h"

namespace algolib {
namespace {

using nlohmann::json;

std::string ToLowerAscii(std::string value) {
    for (char& ch : value) {
        ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    }
    return value;
}

std::vector<std::string> SplitWhitespaceTokens(const std::string& text) {
    std::vector<std::string> tokens;
    std::string current;
    for (char ch : text) {
        if (std::isspace(static_cast<unsigned char>(ch))) {
            if (!current.empty()) {
                tokens.push_back(current);
                current.clear();
            }
            continue;
        }
        current.push_back(ch);
    }
    if (!current.empty()) {
        tokens.push_back(current);
    }
    return tokens;
}

std::int64_t HashToken(const std::string& token) {
    std::uint64_t hash_value = 1469598103934665603ULL;
    for (unsigned char ch : token) {
        hash_value ^= ch;
        hash_value *= 1099511628211ULL;
    }
    return static_cast<std::int64_t>(100 + (hash_value % 1900));
}

Status ValidateRectangularArray(const json& value,
                                std::vector<std::int64_t>* shape,
                                const std::string& path) {
    if (!value.is_array()) {
        if (value.is_number() || value.is_boolean() || value.is_string()) {
            return Status::Ok();
        }
        return Status::Error(ErrorCode::kPreprocessFailed,
                             "tensor_from_json only supports scalar or array JSON values. path=" +
                                 path);
    }

    shape->push_back(static_cast<std::int64_t>(value.size()));
    if (value.empty()) {
        return Status::Ok();
    }

    std::vector<std::int64_t> first_child_shape;
    auto first_status =
        ValidateRectangularArray(value.front(), &first_child_shape, path + "[0]");
    if (!first_status.ok()) {
        return first_status;
    }

    for (std::size_t index = 1; index < value.size(); ++index) {
        std::vector<std::int64_t> child_shape;
        auto child_status = ValidateRectangularArray(
            value.at(index), &child_shape, path + "[" + std::to_string(index) + "]");
        if (!child_status.ok()) {
            return child_status;
        }
        if (child_shape != first_child_shape) {
            return Status::Error(
                ErrorCode::kPreprocessFailed,
                "tensor_from_json requires rectangular arrays. path=" + path);
        }
    }

    shape->insert(shape->end(), first_child_shape.begin(), first_child_shape.end());
    return Status::Ok();
}

void FlattenNumericValues(const json& value,
                          std::vector<double>* numeric_values,
                          bool* all_integer) {
    if (value.is_array()) {
        for (const auto& item : value) {
            FlattenNumericValues(item, numeric_values, all_integer);
        }
        return;
    }

    if (value.is_boolean()) {
        numeric_values->push_back(value.get<bool>() ? 1.0 : 0.0);
        return;
    }

    if (value.is_number_integer()) {
        numeric_values->push_back(static_cast<double>(value.get<std::int64_t>()));
        return;
    }

    *all_integer = false;
    numeric_values->push_back(value.get<double>());
}

Result<TensorBlob> BuildTensorFromJsonValue(const std::string& tensor_name,
                                            const json& value) {
    std::vector<std::int64_t> shape;
    auto shape_status = ValidateRectangularArray(value, &shape, "$");
    if (!shape_status.ok()) {
        return shape_status;
    }

    TensorBlob tensor;
    tensor.name = tensor_name;
    tensor.shape = shape;
    tensor.values = value;

    if (value.is_string()) {
        tensor.dtype = TensorDataType::kString;
        if (tensor.shape.empty()) {
            tensor.shape.push_back(1);
        }
        return tensor;
    }

    if (!(value.is_number() || value.is_boolean() || value.is_array())) {
        return Status::Error(
            ErrorCode::kPreprocessFailed,
            "tensor_from_json only supports numeric, boolean, string or array JSON values.");
    }

    bool all_integer = true;
    std::vector<double> numeric_values;
    FlattenNumericValues(value, &numeric_values, &all_integer);
    tensor.dtype = all_integer ? TensorDataType::kInt64 : TensorDataType::kFloat32;
    if (tensor.shape.empty()) {
        tensor.shape.push_back(1);
    }
    return tensor;
}

Result<std::vector<std::string>> LoadLabels(const std::filesystem::path& label_map_path) {
    auto label_map_result = JsonUtils::ReadJsonFile(label_map_path);
    if (!label_map_result.ok()) {
        return Status::Error(
            ErrorCode::kPostprocessFailed,
            "Failed to load label map " + label_map_path.generic_string() + ": " +
                label_map_result.status().ToString());
    }
    if (!label_map_result.value().is_object()) {
        return Status::Error(ErrorCode::kPostprocessFailed,
                             "label_map.json must be a JSON object.");
    }

    std::vector<std::pair<int, std::string>> indexed_labels;
    for (auto it = label_map_result.value().begin(); it != label_map_result.value().end(); ++it) {
        if (!it.value().is_string()) {
            return Status::Error(ErrorCode::kPostprocessFailed,
                                 "label_map.json values must be strings.");
        }
        indexed_labels.emplace_back(std::stoi(it.key()), it.value().get<std::string>());
    }
    std::sort(indexed_labels.begin(), indexed_labels.end(),
              [](const auto& left, const auto& right) { return left.first < right.first; });

    std::vector<std::string> labels;
    for (const auto& [index, label] : indexed_labels) {
        (void)index;
        labels.push_back(label);
    }
    return labels;
}

Result<std::vector<double>> ExtractScoreVector(const TensorBlob& tensor) {
    json candidate = tensor.values;
    if (candidate.is_array() && !candidate.empty() && candidate.front().is_array()) {
        candidate = candidate.front();
    }
    if (!candidate.is_array() || candidate.empty()) {
        return Status::Error(ErrorCode::kPostprocessFailed,
                             "classification_postprocess expects a non-empty 1D score array.");
    }

    std::vector<double> scores;
    for (const auto& item : candidate) {
        if (!item.is_number()) {
            return Status::Error(
                ErrorCode::kPostprocessFailed,
                "classification_postprocess expects numeric scores.");
        }
        scores.push_back(item.get<double>());
    }
    return scores;
}

std::vector<double> NormalizeScores(const std::vector<double>& scores) {
    const bool looks_like_probability =
        std::all_of(scores.begin(), scores.end(), [](double score) {
            return score >= 0.0 && score <= 1.0;
        }) &&
        std::fabs(std::accumulate(scores.begin(), scores.end(), 0.0) - 1.0) < 1e-6;
    if (looks_like_probability) {
        return scores;
    }

    const double max_score = *std::max_element(scores.begin(), scores.end());
    std::vector<double> exp_scores;
    exp_scores.reserve(scores.size());
    double total = 0.0;
    for (double score : scores) {
        const double exp_score = std::exp(score - max_score);
        exp_scores.push_back(exp_score);
        total += exp_score;
    }

    for (double& score : exp_scores) {
        score /= total;
    }
    return exp_scores;
}

}  // namespace

std::string ToString(TensorDataType dtype) {
    switch (dtype) {
        case TensorDataType::kFloat32:
            return "float32";
        case TensorDataType::kInt64:
            return "int64";
        case TensorDataType::kString:
            return "string";
    }
    return "unknown";
}

Result<PreprocessConfig> OnnxPipeline::LoadPreprocessConfig(
    const std::filesystem::path& config_path) const {
    auto yaml_result = YamlUtils::LoadYamlFile(config_path);
    if (!yaml_result.ok()) {
        return Status::Error(
            ErrorCode::kPreprocessFailed,
            "Failed to load preprocess config " + config_path.generic_string() + ": " +
                yaml_result.status().ToString());
    }

    const YAML::Node& root = yaml_result.value();
    if (!root || !root.IsMap()) {
        return Status::Error(ErrorCode::kPreprocessFailed,
                             "preprocess.yaml root must be a map.");
    }

    PreprocessConfig config;
    if (root["type"]) {
        config.type = root["type"].as<std::string>();
    }
    if (root["lowercase"]) {
        config.lowercase = root["lowercase"].as<bool>();
    }
    if (root["input_field"]) {
        config.input_field = root["input_field"].as<std::string>();
    }
    if (root["tensor_name"]) {
        config.tensor_name = root["tensor_name"].as<std::string>();
    }

    static const std::vector<std::string> kSupportedTypes = {
        "no_op",
        "tensor_from_json",
        "text_tokenization",
    };
    if (std::find(kSupportedTypes.begin(), kSupportedTypes.end(), config.type) ==
        kSupportedTypes.end()) {
        return Status::Error(ErrorCode::kPreprocessFailed,
                             "Unsupported preprocess type: " + config.type + ".");
    }
    return config;
}

Result<PostprocessConfig> OnnxPipeline::LoadPostprocessConfig(
    const std::filesystem::path& config_path) const {
    auto yaml_result = YamlUtils::LoadYamlFile(config_path);
    if (!yaml_result.ok()) {
        return Status::Error(
            ErrorCode::kPostprocessFailed,
            "Failed to load postprocess config " + config_path.generic_string() + ": " +
                yaml_result.status().ToString());
    }

    const YAML::Node& root = yaml_result.value();
    if (!root || !root.IsMap()) {
        return Status::Error(ErrorCode::kPostprocessFailed,
                             "postprocess.yaml root must be a map.");
    }

    PostprocessConfig config;
    if (root["type"]) {
        config.type = root["type"].as<std::string>();
    }
    if (root["top_k"]) {
        config.top_k = root["top_k"].as<int>();
    }
    if (root["tensor_name"]) {
        config.tensor_name = root["tensor_name"].as<std::string>();
    } else if (config.type == "classification_postprocess") {
        config.tensor_name = "logits";
    }

    static const std::vector<std::string> kSupportedTypes = {
        "no_op",
        "classification_postprocess",
    };
    if (std::find(kSupportedTypes.begin(), kSupportedTypes.end(), config.type) ==
        kSupportedTypes.end()) {
        return Status::Error(ErrorCode::kPostprocessFailed,
                             "Unsupported postprocess type: " + config.type + ".");
    }
    if (config.top_k <= 0) {
        return Status::Error(ErrorCode::kPostprocessFailed,
                             "top_k must be greater than 0.");
    }
    return config;
}

std::vector<std::string> OnnxPipeline::ExpectedInputTensorNames(
    const PreprocessConfig& config) const {
    if (config.type == "text_tokenization") {
        return {"input_ids", "attention_mask"};
    }
    return {config.tensor_name};
}

std::vector<std::string> OnnxPipeline::ExpectedOutputTensorNames(
    const PostprocessConfig& config) const {
    if (config.type == "classification_postprocess") {
        return {config.tensor_name};
    }
    return {config.tensor_name};
}

Result<std::vector<TensorBlob>> OnnxPipeline::RunPreprocess(const PreprocessConfig& config,
                                                            const AlgorithmRequest& request) const {
    if (config.type == "no_op") {
        TensorBlob tensor;
        tensor.name = config.tensor_name;
        tensor.dtype = TensorDataType::kString;
        tensor.shape = {1};
        tensor.values = request.inputs;
        tensor.metadata = {{"source_json", request.inputs}};
        return std::vector<TensorBlob>{tensor};
    }

    if (config.type == "tensor_from_json") {
        const json& source_value =
            request.inputs.is_object() && request.inputs.contains("tensor")
                ? request.inputs.at("tensor")
                : request.inputs;
        auto tensor_result = BuildTensorFromJsonValue(config.tensor_name, source_value);
        if (!tensor_result.ok()) {
            return tensor_result.status();
        }
        tensor_result.value().metadata = {{"source_json", request.inputs}};
        return std::vector<TensorBlob>{tensor_result.value()};
    }

    if (config.type == "text_tokenization") {
        if (!request.inputs.is_object() || !request.inputs.contains(config.input_field) ||
            !request.inputs.at(config.input_field).is_string()) {
            return Status::Error(
                ErrorCode::kPreprocessFailed,
                "text_tokenization requires string field `" + config.input_field + "`.");
        }

        std::string text = request.inputs.at(config.input_field).get<std::string>();
        if (config.lowercase) {
            text = ToLowerAscii(text);
        }

        std::vector<std::string> tokens = SplitWhitespaceTokens(text);
        if (tokens.empty()) {
            tokens.push_back("[EMPTY]");
        }

        std::vector<std::int64_t> input_ids;
        input_ids.push_back(101);
        for (const auto& token : tokens) {
            input_ids.push_back(HashToken(token));
        }
        input_ids.push_back(102);

        std::vector<std::int64_t> attention_mask(input_ids.size(), 1);

        TensorBlob ids_tensor;
        ids_tensor.name = "input_ids";
        ids_tensor.dtype = TensorDataType::kInt64;
        ids_tensor.shape = {1, static_cast<std::int64_t>(input_ids.size())};
        ids_tensor.values = json::array({input_ids});
        ids_tensor.metadata = {{"source_text", text}};

        TensorBlob mask_tensor;
        mask_tensor.name = "attention_mask";
        mask_tensor.dtype = TensorDataType::kInt64;
        mask_tensor.shape = {1, static_cast<std::int64_t>(attention_mask.size())};
        mask_tensor.values = json::array({attention_mask});
        mask_tensor.metadata = {{"source_text", text}};

        return std::vector<TensorBlob>{ids_tensor, mask_tensor};
    }

    return Status::Error(ErrorCode::kPreprocessFailed,
                         "Unsupported preprocess type during execution: " + config.type + ".");
}

Result<json> OnnxPipeline::RunPostprocess(const PostprocessConfig& config,
                                          const std::vector<TensorBlob>& output_tensors,
                                          const std::filesystem::path& label_map_path) const {
    if (output_tensors.empty()) {
        return Status::Error(ErrorCode::kPostprocessFailed,
                             "ONNX session did not return any output tensor.");
    }

    auto tensor_it = std::find_if(output_tensors.begin(), output_tensors.end(),
                                  [&config](const TensorBlob& tensor) {
                                      return tensor.name == config.tensor_name;
                                  });
    if (tensor_it == output_tensors.end()) {
        return Status::Error(ErrorCode::kPostprocessFailed,
                             "Expected output tensor was not found: " + config.tensor_name + ".");
    }

    if (config.type == "no_op") {
        return tensor_it->values;
    }

    if (config.type == "classification_postprocess") {
        auto labels_result = LoadLabels(label_map_path);
        if (!labels_result.ok()) {
            return labels_result.status();
        }

        auto scores_result = ExtractScoreVector(*tensor_it);
        if (!scores_result.ok()) {
            return scores_result.status();
        }

        const std::vector<double> probabilities = NormalizeScores(scores_result.value());
        if (probabilities.empty()) {
            return Status::Error(ErrorCode::kPostprocessFailed,
                                 "classification_postprocess received empty score vector.");
        }

        std::vector<std::size_t> indices(probabilities.size());
        std::iota(indices.begin(), indices.end(), 0U);
        std::sort(indices.begin(), indices.end(),
                  [&probabilities](std::size_t left, std::size_t right) {
                      return probabilities[left] > probabilities[right];
                  });

        if (indices.front() >= labels_result.value().size()) {
            return Status::Error(
                ErrorCode::kPostprocessFailed,
                "label_map.json does not cover the highest scoring class index.");
        }

        if (config.top_k == 1) {
            return json{
                {"label", labels_result.value().at(indices.front())},
                {"confidence", probabilities.at(indices.front())},
            };
        }

        json predictions = json::array();
        const std::size_t limit =
            std::min<std::size_t>(static_cast<std::size_t>(config.top_k), indices.size());
        for (std::size_t position = 0; position < limit; ++position) {
            const std::size_t index = indices.at(position);
            if (index >= labels_result.value().size()) {
                continue;
            }
            predictions.push_back({
                {"label", labels_result.value().at(index)},
                {"confidence", probabilities.at(index)},
            });
        }
        return json{{"predictions", predictions}};
    }

    return Status::Error(ErrorCode::kPostprocessFailed,
                         "Unsupported postprocess type during execution: " + config.type + ".");
}

}  // namespace algolib
