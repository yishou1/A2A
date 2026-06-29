#pragma once

#include <string>

namespace algolib {

// 中文注释: 统一维护 CLI、注册表和校验阶段会返回的错误码
enum class ErrorCode {
    kOk = 0,
    kAlgorithmNotFound,
    kAlgorithmNotActive,
    kBackendTypeMismatch,
    kInputSchemaInvalid,
    kOutputSchemaInvalid,
    kGoldenCaseFailed,
    kInvalidAlgorithmCard,
    kOnnxModelNotFound,
    kOnnxLoadFailed,
    kOnnxRuntimeError,
    kOnnxInputTensorMismatch,
    kOnnxOutputTensorMismatch,
    kPreprocessFailed,
    kPostprocessFailed,
    kTokenizerNotSupported,
    kServiceNotReady,
    kServiceUnavailable,
    kServiceTimeout,
    kServiceHttpError,
    kServiceMetadataMismatch,
    kServiceResponseInvalid,
    kServiceOutputSchemaInvalid,
    kIoError,
    kYamlParseError,
    kJsonParseError,
    kRegistryConflict,
    kRegistryStoreError,
    kStatusTransitionInvalid,
    kUnsupportedBackendType,
    kMissingRequiredField,
    kMissingRequiredFile,
    kInvalidArgument
};

std::string ToString(ErrorCode code);

}  // namespace algolib
