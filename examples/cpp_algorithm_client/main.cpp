#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>

#include "algolib/core/error_code.h"
#include "algolib/io/file_utils.h"
#include "algolib/io/json_utils.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"
#include "algolib/runtime/execution_coordinator.h"
#include "algolib/runtime/runtime_runner_cache.h"

namespace fs = std::filesystem;
using namespace algolib;

namespace {

// 检查返回值是否成功；失败时把算法库错误转换为 C++ 异常。
template <typename T>
void RequireOk(const Result<T>& result, const std::string& action) {
    if (!result.ok()) {
        throw std::runtime_error(action + ": " + result.status().ToString());
    }
}

void RequireOk(const Status& status, const std::string& action) {
    if (!status.ok()) {
        throw std::runtime_error(action + ": " + status.ToString());
    }
}

// 确保请求指定的算法已经注册并处于 active 状态。
// 第一次调用时从算法包目录注册；以后调用直接复用注册表中的记录。
void EnsureRegisteredAndActive(AlgorithmRegistry& registry,
                               const AlgorithmKey& key,
                               const fs::path& package_path) {
    // 通过“算法 ID + 版本 + 后端类型”查询算法。
    const auto existing = registry.Get(key);
    if (!existing.ok()) {
        // 非“算法不存在”错误直接返回，避免掩盖注册表损坏等问题。
        if (existing.status().code() != ErrorCode::kAlgorithmNotFound) {
            RequireOk(existing, "query algorithm");
        }

        // 算法不存在：读取 package_path 中的 Algorithm Card 并注册。
        const auto registered = registry.Register(package_path);
        RequireOk(registered, "register algorithm");

        // 防止算法包和请求中填写的 ID、版本或后端不一致。
        if (!(registered.value().key == key)) {
            throw std::runtime_error(
                "The algorithm package identity does not match the request.");
        }
    }

    // 只有 active 状态的算法可以被 ExecutionCoordinator 执行。
    RequireOk(registry.Activate(key), "activate algorithm");
}

void PrintUsage() {
    std::cerr
        << "用法：\n"
        << "  algorithm_call_demo <algorithm_package_path> <request_json_path> "
           "[registry_json_path]\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    // 必填参数：算法包目录、请求 JSON；可选参数：注册表文件路径。
    if (argc != 3 && argc != 4) {
        PrintUsage();
        return 1;
    }

    try {
        // argv[1]：算法包目录，例如 examples/xxx/1.0.0。
        const fs::path package_path = argv[1];

        // argv[2]：完整 AlgorithmRequest 请求 JSON 文件。
        const fs::path request_path = argv[2];

        // argv[3]：可选注册表路径；不传时使用默认演示注册表。
        const fs::path registry_path =
            argc == 4 ? fs::path(argv[3])
                      : fs::path(".algolib/algorithm_call_demo_registry.json");

        // 解析并检查请求文件路径是否存在。
        const auto normalized_request_path =
            FileUtils::NormalizeInputPath(request_path);
        RequireOk(normalized_request_path, "resolve request file");

        // 从磁盘读取请求 JSON。
        const auto request_json = JsonUtils::ReadJsonFile(normalized_request_path.value());
        RequireOk(request_json, "read request file");

        // 把 JSON 转换为统一 AlgorithmRequest，并校验必填字段。
        const auto parsed_request = AlgorithmRequestFromJson(request_json.value());
        RequireOk(parsed_request, "parse request");

        // 从请求中取出算法 ID、版本、后端类型，组成唯一算法标识。
        const AlgorithmRequest& request = parsed_request.value();
        const AlgorithmKey key{
            request.algorithm_id,
            request.version,
            request.backend_type,
        };

        // 加载持久化注册表，并保证目标算法已经注册、激活。
        AlgorithmRegistry registry(registry_path);
        RequireOk(registry.Reload(), "load registry");
        EnsureRegisteredAndActive(registry, key, package_path);

        // 缓存已加载的运行器；ONNX 后端可复用模型 Session。
        RuntimeRunnerCache runner_cache;

        // 统一执行入口会根据 request.backend_type 自动选择：
        //   onnx                -> OnnxRunner
        //   python_http_service -> PythonHttpRunner
        ExecutionCoordinator coordinator(
            registry,
            ".algolib/algorithm_call_demo_audit.jsonl",
            &runner_cache);

        // 真正执行算法。result.outputs 是具体算法输出，result.error 是错误。
        const AlgorithmResult result = coordinator.Run(request);

        // 打印包含状态、算法输出和运行信息的完整统一结果。
        std::cout << ToJson(result).dump(2) << '\n';
        return result.ok ? 0 : 1;
    } catch (const std::exception& exception) {
        // 文件、注册或请求解析阶段发生异常时，统一输出 JSON 错误。
        std::cerr << nlohmann::json{
                         {"ok", false},
                         {"error_code", "CLIENT_ERROR"},
                         {"message", exception.what()},
                     }
                         .dump(2)
                  << '\n';
        return 1;
    }
}
