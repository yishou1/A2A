#pragma once

#include <string>

namespace algolib {

// 中文注释：审计日志按 SPEC 记录输入输出摘要，这里提供最小可复用的 SHA-256 计算接口。
std::string ComputeSha256Hex(const std::string& input);

}  // namespace algolib
