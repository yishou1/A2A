#pragma once

#include <string>
#include <string_view>

#include "algolib/core/status.h"

namespace algolib {

enum class AlgorithmStatus {  // 中文注释: 状态机与 SPEC 保持一致 用于注册表中的生命周期管理
    kDraft = 0,
    kValidated,
    kActive,
    kDisabled,
    kDeleted
};

std::string ToString(AlgorithmStatus status);
Result<AlgorithmStatus> ParseAlgorithmStatus(std::string_view raw_value);

}  // namespace algolib
