#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace algolib {

enum class TensorDataType {
    kFloat32,
    kInt64,
    kString,
};

// 中文注释：TensorBlob 用统一结构承接预处理、session 和后处理之间的张量数据。
struct TensorBlob {
    std::string name;
    TensorDataType dtype = TensorDataType::kFloat32;
    std::vector<std::int64_t> shape;
    nlohmann::json values;
    nlohmann::json metadata;
};

std::string ToString(TensorDataType dtype);

}  // namespace algolib
