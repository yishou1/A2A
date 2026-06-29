#pragma once

#include <string>
#include <tuple>

#include "algolib/core/backend_type.h"

namespace algolib {

// 中文注释: 算法唯一键由 algorithm_id + version + backend_type 组成
struct AlgorithmKey {
    std::string algorithm_id;
    std::string version;
    BackendType backend_type = BackendType::kOnnx;

    std::string ToUniqueString() const {
        return algorithm_id + "|" + version + "|" + ToString(backend_type);
    }

    friend bool operator==(const AlgorithmKey& lhs, const AlgorithmKey& rhs) {
        return std::tie(lhs.algorithm_id, lhs.version, lhs.backend_type) ==
               std::tie(rhs.algorithm_id, rhs.version, rhs.backend_type);
    }

    friend bool operator<(const AlgorithmKey& lhs, const AlgorithmKey& rhs) {
        return std::tie(lhs.algorithm_id, lhs.version, lhs.backend_type) <
               std::tie(rhs.algorithm_id, rhs.version, rhs.backend_type);
    }
};

}  // namespace algolib
