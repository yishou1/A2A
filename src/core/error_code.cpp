#include "algolib/core/error_code.h"

namespace algolib {

std::string ToString(ErrorCode code) {
    switch (code) {
        case ErrorCode::kOk:
            return "OK";
        case ErrorCode::kAlgorithmNotFound:
            return "ALGORITHM_NOT_FOUND";
        case ErrorCode::kAlgorithmNotActive:
            return "ALGORITHM_NOT_ACTIVE";
        case ErrorCode::kBackendTypeMismatch:
            return "BACKEND_TYPE_MISMATCH";
        case ErrorCode::kInputSchemaInvalid:
            return "INPUT_SCHEMA_INVALID";
        case ErrorCode::kOutputSchemaInvalid:
            return "OUTPUT_SCHEMA_INVALID";
        case ErrorCode::kGoldenCaseFailed:
            return "GOLDEN_CASE_FAILED";
        case ErrorCode::kInvalidAlgorithmCard:
            return "INVALID_ALGORITHM_CARD";
        case ErrorCode::kOnnxModelNotFound:
            return "ONNX_MODEL_NOT_FOUND";
        case ErrorCode::kOnnxLoadFailed:
            return "ONNX_LOAD_FAILED";
        case ErrorCode::kOnnxRuntimeError:
            return "ONNX_RUNTIME_ERROR";
        case ErrorCode::kOnnxInputTensorMismatch:
            return "ONNX_INPUT_TENSOR_MISMATCH";
        case ErrorCode::kOnnxOutputTensorMismatch:
            return "ONNX_OUTPUT_TENSOR_MISMATCH";
        case ErrorCode::kPreprocessFailed:
            return "PREPROCESS_FAILED";
        case ErrorCode::kPostprocessFailed:
            return "POSTPROCESS_FAILED";
        case ErrorCode::kTokenizerNotSupported:
            return "TOKENIZER_NOT_SUPPORTED";
        case ErrorCode::kServiceNotReady:
            return "SERVICE_NOT_READY";
        case ErrorCode::kServiceUnavailable:
            return "SERVICE_UNAVAILABLE";
        case ErrorCode::kServiceTimeout:
            return "SERVICE_TIMEOUT";
        case ErrorCode::kServiceHttpError:
            return "SERVICE_HTTP_ERROR";
        case ErrorCode::kServiceMetadataMismatch:
            return "SERVICE_METADATA_MISMATCH";
        case ErrorCode::kServiceResponseInvalid:
            return "SERVICE_RESPONSE_INVALID";
        case ErrorCode::kServiceOutputSchemaInvalid:
            return "SERVICE_OUTPUT_SCHEMA_INVALID";
        case ErrorCode::kIoError:
            return "IO_ERROR";
        case ErrorCode::kYamlParseError:
            return "YAML_PARSE_ERROR";
        case ErrorCode::kJsonParseError:
            return "JSON_PARSE_ERROR";
        case ErrorCode::kRegistryConflict:
            return "REGISTRY_CONFLICT";
        case ErrorCode::kRegistryStoreError:
            return "REGISTRY_STORE_ERROR";
        case ErrorCode::kStatusTransitionInvalid:
            return "STATUS_TRANSITION_INVALID";
        case ErrorCode::kUnsupportedBackendType:
            return "UNSUPPORTED_BACKEND_TYPE";
        case ErrorCode::kMissingRequiredField:
            return "MISSING_REQUIRED_FIELD";
        case ErrorCode::kMissingRequiredFile:
            return "MISSING_REQUIRED_FILE";
        case ErrorCode::kInvalidArgument:
            return "INVALID_ARGUMENT";
    }

    return "UNKNOWN_ERROR";
}

}  // namespace algolib
