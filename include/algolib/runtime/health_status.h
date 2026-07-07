#pragma once

#include <string>

namespace algolib {

// 中文注释：HealthStatus 让 runner 对外暴露“是否已加载可运行”的最小健康信息。
struct HealthStatus {
    bool ok = false;
    std::string status = "unknown";
    std::string message;
};

}  // namespace algolib
