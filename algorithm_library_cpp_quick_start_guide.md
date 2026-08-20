# C++ 调用算法库快速使用说明

本文面向第一次接入本项目的开发人员，说明如何用 C++ 调用两类算法后端：

- **ONNX**：C++ 进程直接加载 `model.onnx` 并执行推理。
- **Python Service**：C++ 通过 HTTP 调用已经启动的 Python 算法服务。

两类后端共用同一套调用流程：

```text
注册算法包 -> 激活算法 -> 构造请求 -> ExecutionCoordinator::Run()
                                      |
                                      +-- ONNX -> OnnxRunner
                                      +-- Python -> PythonHttpRunner -> /health -> /predict
```

## 最直接的调用方法（先看这里）

项目现在提供了一个已经接好算法库的 C++ 示例程序：

```text
build-zsl-verify\algorithm_call_demo.exe
```

它的调用格式是：

```text
algorithm_call_demo.exe <算法包目录> <请求 JSON> [注册表路径]
```

程序会自动完成：

```text
读取请求 -> 首次调用时注册 -> 激活 -> 选择后端 -> 执行 -> 输出结果
```

### 调用 ONNX 算法

从仓库根目录执行：

```powershell
.\build-zsl-verify\algorithm_call_demo.exe `
  .\examples\compliance_risk_scorer_onnx\1.0.0 `
  .\examples\cpp_algorithm_client\requests\compliance_risk_scorer_onnx.json
```

不需要启动 Python。结果中出现下面内容代表真实 ONNX 推理成功：

```json
"ok": true,
"session_backend": "onnxruntime"
```

### 调用 Python Service 算法

第一个终端启动服务：

```powershell
conda activate algolib
python services\execution_rule_matcher\app\main.py
```

第二个终端从仓库根目录调用：

```powershell
.\build-zsl-verify\algorithm_call_demo.exe `
  .\examples\execution_rule_matcher\1.0.0 `
  .\examples\execution_rule_matcher\1.0.0\golden_cases\case_001_request.json
```

结果中出现下面内容代表 Python Service 调用成功：

```json
"ok": true,
"backend_type": "python_http_service"
```

默认注册信息持久化在：

```text
.algolib\algorithm_call_demo_registry.json
```

以后更换算法时，主要替换两个参数：

1. 对应的 `examples/<algorithm_id>/<version>` 算法包目录。
2. 符合该算法 `input.schema.json` 的请求 JSON 文件。

可直接查看和修改的完整 C++ 调用代码位于：

```text
examples\cpp_algorithm_client\main.cpp
```

## 1. 基础环境

### C++ 基本要求

- Windows x64。
- 支持 C++17 的编译器，推荐 Visual Studio 2022 C++ 工具集。
- CMake 3.20 或更高版本。
- ONNX Runtime C++ SDK 1.29。
- 项目应从仓库根目录运行，避免算法包相对路径失效。

当前机器已准备好：

```text
ONNX SDK：D:\Desktop\algorithm repo1\build-deps\onnxruntime-win-x64-1.29.0
已验证程序：D:\Desktop\algorithm repo1\build-zsl-verify\algolib.exe
运行时 DLL：D:\Desktop\algorithm repo1\build-zsl-verify\onnxruntime.dll
```

`onnxruntime.dll` 1.29 必须和生成的 exe 位于同一目录。项目的 CMake 会为项目内的 `algolib.exe`、服务程序和测试程序自动复制该 DLL。

### Python 基本要求

- Python 3.11。
- 当前已创建 Conda 环境 `algolib`。
- 完整依赖定义在 `services/requirements.txt`。
- Python 服务监听的本机端口必须可用，并与算法卡中的 URL 一致。

```powershell
conda activate algolib
python -m pip install -r services\requirements.txt
```

通常只需激活现有环境，不必重复安装依赖。

## 2. 构建支持 ONNX 的 C++ 项目

在 Visual Studio Developer PowerShell 中，从仓库根目录执行：

```powershell
$OrtRoot = "D:\Desktop\algorithm repo1\build-deps\onnxruntime-win-x64-1.29.0"

cmake -S . -B build-ort -G Ninja `
  -DALGOLIB_WITH_ONNXRUNTIME=ON `
  -DALGOLIB_ONNXRUNTIME_ROOT="$OrtRoot"

cmake --build build-ort --parallel
ctest --test-dir build-ort --output-on-failure
```

如果没有安装 Ninja，可省略 `-G Ninja`，使用 Visual Studio 默认生成器。

成功后应看到：

```text
build-ort\algolib.exe
build-ort\algorithm_call_demo.exe
build-ort\onnxruntime.dll
```

本机已经有验证通过的 `build-zsl-verify`，如果不需要重新编译，可将下文命令中的 `build-ort` 直接替换为 `build-zsl-verify`。

## 3. 先用 C++ CLI 验证

CLI 本身就是 C++ 程序。正式写业务代码前，先用它验证环境和算法包最简单。

### 3.1 调用 ONNX 算法

先创建 `request_onnx.json`：

```json
{
  "request_id": "req-onnx-001",
  "trace_id": "trace-onnx-001",
  "algorithm_id": "compliance_risk_scorer_onnx",
  "version": "1.0.0",
  "backend_type": "onnx",
  "inputs": {
    "features": [[0.0, 1.0, 0.2, 0.0, 2.0, 0.0]]
  },
  "params": {}
}
```

注册、激活并运行：

```powershell
$env:ALGOLIB_REGISTRY_PATH = "$PWD\runtime\quickstart_registry.json"

.\build-ort\algolib.exe register .\examples\compliance_risk_scorer_onnx\1.0.0
.\build-ort\algolib.exe activate compliance_risk_scorer_onnx 1.0.0 onnx
.\build-ort\algolib.exe run .\request_onnx.json
```

结果中出现下面字段，表示使用了真实 ONNX Runtime：

```json
"session_backend": "onnxruntime"
```

### 3.2 调用 Python Service 算法

第一个终端启动 Python 服务：

```powershell
conda activate algolib
python services\execution_rule_matcher\app\main.py
```

该服务默认监听：

```text
GET  http://127.0.0.1:9010/health
POST http://127.0.0.1:9010/predict
```

第二个终端执行 C++ CLI：

```powershell
$env:ALGOLIB_REGISTRY_PATH = "$PWD\runtime\quickstart_registry.json"

.\build-ort\algolib.exe register .\examples\execution_rule_matcher\1.0.0
.\build-ort\algolib.exe activate execution_rule_matcher 1.0.0 python_http_service
.\build-ort\algolib.exe run `
  .\examples\execution_rule_matcher\1.0.0\golden_cases\case_001_request.json
```

Python Service 必须保持运行。C++ 会先访问 `/health`，再把请求发送到 `/predict`。

## 4. 在自己的 C++ 程序中调用

如果调用程序位于本项目内，在根 `CMakeLists.txt` 的 `algolib_copy_onnxruntime_dll_if_needed` 函数定义之后添加（最简单是放在文件末尾）：

```cmake
# 把 my_algorithm_app.cpp 编译成 my_algorithm_app.exe
add_executable(my_algorithm_app my_algorithm_app.cpp)

# 将算法库 algolib 链接到自己的程序，否则无法使用算法库的 C++ 接口
target_link_libraries(my_algorithm_app PRIVATE algolib)

# Windows 下把正确版本的 onnxruntime.dll 复制到新生成的 exe 旁边
algolib_copy_onnxruntime_dll_if_needed(my_algorithm_app)
```

两类后端使用相同的 `RunAlgorithm()` 函数：

项目中可运行的完整实现见 `examples/cpp_algorithm_client/main.cpp`。下面是核心调用逻辑：

```cpp
// 标准库：文件路径、控制台输出、异常和数据移动
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <utility>

// 使用 JSON 表示不同算法的输入和输出
#include <nlohmann/json.hpp>

// 算法库错误码
#include "algolib/core/error_code.h"
// 算法注册表：记录已注册、已激活的算法
#include "algolib/registry/algorithm_registry.h"
// 统一算法请求
#include "algolib/runtime/algorithm_request.h"
// 统一算法结果
#include "algolib/runtime/algorithm_result.h"
// 统一执行入口：根据后端类型选择 ONNX 或 Python Service
#include "algolib/runtime/execution_coordinator.h"

using namespace algolib;

// 通用算法调用函数。
//
// 输入：
//   package：算法包目录，例如 examples/xxx/1.0.0
//   key：算法 ID、版本、后端类型
//   inputs：真正交给算法的 JSON 输入
//
// 输出：
//   AlgorithmResult，其中 result.outputs 是算法的具体输出
AlgorithmResult RunAlgorithm(const std::filesystem::path& package,
                             const AlgorithmKey& key,
                             nlohmann::json inputs) {
    // 打开持久化注册表；程序结束后，注册信息仍保存在该文件中。
    AlgorithmRegistry registry("runtime/cpp_registry.json");

    // 从磁盘加载以前注册的算法。加载失败就抛出异常、停止调用。
    if (auto status = registry.Reload(); !status.ok()) {
        throw std::runtime_error(status.ToString());
    }

    // 使用“算法 ID + 版本 + 后端”查询算法是否已经注册。
    auto existing = registry.Get(key);
    if (!existing.ok()) {
        // 如果不是“算法不存在”，说明发生了其他注册表错误。
        if (existing.status().code() != ErrorCode::kAlgorithmNotFound) {
            throw std::runtime_error(existing.status().ToString());
        }

        // 算法不存在时，从 package 指定的算法包目录完成首次注册。
        if (auto registered = registry.Register(package); !registered.ok()) {
            throw std::runtime_error(registered.status().ToString());
        }
    }

    // 注册后必须激活，只有 active 状态的算法才能执行。
    if (auto activated = registry.Activate(key); !activated.ok()) {
        throw std::runtime_error(activated.status().ToString());
    }

    // 创建统一算法请求。
    AlgorithmRequest request;

    // 指定算法 ID、版本和后端类型。
    request.algorithm_id = key.algorithm_id;
    request.version = key.version;
    request.backend_type = key.backend_type;

    // 把调用方提供的 JSON 数据放入算法输入字段。
    request.inputs = std::move(inputs);

    // 本例没有额外运行参数，所以使用空 JSON 对象。
    request.params = nlohmann::json::object();

    // 创建执行调度器：
    //   kOnnx              -> OnnxRunner
    //   kPythonHttpService -> PythonHttpRunner
    ExecutionCoordinator coordinator(registry);

    // 执行算法并返回结果；调用方通过 result.outputs 取得具体输出。
    return coordinator.Run(request);
}
```

### 4.1 调用 ONNX：明确输入和输出

```cpp
// 1. 指定算法 ID、版本和 ONNX 后端。
AlgorithmKey onnx_key{
    "compliance_risk_scorer_onnx",
    "1.0.0",
    BackendType::kOnnx
};

// 2. 准备算法输入。
// 对应 JSON：
// {
//   "features": [[0.0, 1.0, 0.2, 0.0, 2.0, 0.0]]
// }
nlohmann::json onnx_inputs = {
    {"features", {
        {0.0, 1.0, 0.2, 0.0, 2.0, 0.0}
    }}
};

// 3. 调用算法。
// onnx_result 是统一结果，onnx_result.outputs 是模型的具体输出。
AlgorithmResult onnx_result = RunAlgorithm(
    "examples/compliance_risk_scorer_onnx/1.0.0",
    onnx_key,
    onnx_inputs
);

// 4. 判断调用是否成功。
if (!onnx_result.ok) {
    if (onnx_result.error) {
        std::cerr << onnx_result.error->code << ": "
                  << onnx_result.error->message << '\n';
    }
} else {
    // 5. 打印算法的具体输出。
    std::cout << "ONNX 算法输出：\n"
              << onnx_result.outputs.dump(2) << '\n';

    // 6. 本算法的输出字段是 risk_probability[0][0]。
    double risk_probability =
        onnx_result.outputs
            .at("risk_probability")
            .at(0)
            .at(0)
            .get<double>();

    std::cout << "风险概率：" << risk_probability << '\n';
}
```

上面的 `onnx_inputs` 是输入，`onnx_result.outputs` 是输出。实际输出类似：

```json
{
  "risk_probability": [[0.3941263258457184]]
}
```

### 4.2 调用 Python Service：明确输入和输出

调用前必须先启动对应的 Python 服务：

```powershell
conda activate algolib
python services\execution_rule_matcher\app\main.py
```

然后在 C++ 中调用：

```cpp
// 1. 指定算法 ID、版本和 Python HTTP Service 后端。
AlgorithmKey python_key{
    "execution_rule_matcher",
    "1.0.0",
    BackendType::kPythonHttpService
};

// 2. 准备发送给 Python Service 的算法输入。
// 字段必须符合该算法包中的 input.schema.json。
nlohmann::json python_inputs = {
    {"phase", "strike"},
    {"situation", {
        {"threat_score", 0.75},
        {"intel_confidence", 0.82},
        {"resource_readiness", 0.81},
        {"communication_quality", 0.90}
    }}
};

// 3. 调用算法。
// RunAlgorithm 内部会通过 PythonHttpRunner 请求 /health 和 /predict。
AlgorithmResult python_result = RunAlgorithm(
    "examples/execution_rule_matcher/1.0.0",
    python_key,
    python_inputs
);

// 4. 判断调用是否成功。
if (!python_result.ok) {
    if (python_result.error) {
        std::cerr << python_result.error->code << ": "
                  << python_result.error->message << '\n';
    }
} else {
    // 5. 打印 Python 算法返回的具体输出。
    std::cout << "Python Service 算法输出：\n"
              << python_result.outputs.dump(2) << '\n';

    // 6. 示例：读取 matched_rules 数组中的规则数量。
    std::size_t matched_rule_count =
        python_result.outputs.at("matched_rules").size();

    std::cout << "匹配规则数量："
              << matched_rule_count << '\n';
}
```

上面的 `python_inputs` 是输入，`python_result.outputs` 是输出。输出的简化结构类似：

```json
{
  "matched_items": ["intel=good", "phase=strike"],
  "matched_rules": [
    {
      "rule_id": "RULE-003",
      "confidence": 1.0
    }
  ],
  "primary_rule": {
    "rule_id": "RULE-003"
  }
}
```

最关键的对应关系是：

```cpp
nlohmann::json inputs = ...;                 // 算法输入
AlgorithmResult result = RunAlgorithm(...);  // 执行算法
nlohmann::json outputs = result.outputs;      // 算法输出
```

## 5. 使用其他算法时修改什么

只需要修改三项：

| 项目 | ONNX | Python Service |
| --- | --- | --- |
| 算法包目录 | `examples/<id>/<version>` | `examples/<id>/<version>` |
| 后端类型 | `BackendType::kOnnx` | `BackendType::kPythonHttpService` |
| 输入 | 符合 `input.schema.json` | 符合 `input.schema.json` |

Python Service 还需要先启动对应的 `services/<id>/app/main.py`，并确认 `algorithm_card.yaml` 中的 `endpoint`、`health_endpoint` 与实际端口一致。

## 6. 常见问题

### 程序找不到 ONNX DLL

确认 1.29 版本的 `onnxruntime.dll` 在 exe 同目录。不要手动删除 `C:\Windows\System32` 中的旧版本 DLL。

### 输出显示 `session_backend: stub`

说明编译时没有真正启用 ONNX Runtime。重新配置 CMake，并确保：

```text
ALGOLIB_WITH_ONNXRUNTIME=ON
ALGOLIB_ONNXRUNTIME_ROOT=<ONNX Runtime 1.29 SDK 目录>
```

### Python Service 无法连接

依次检查：

1. Conda 环境是否为 `algolib`。
2. Python 服务是否已经启动。
3. `/health` 是否返回正常。
4. 服务端口是否与 `algorithm_card.yaml` 一致。
5. 端口是否被其他程序占用。

### 注册时报算法已存在

算法只需注册一次。长期运行的 C++ 程序应先调用 `registry.Get(key)`，不存在时再调用 `Register()`。

## 7. 最小检查清单

- [ ] C++ 使用 C++17，CMake 不低于 3.20。
- [ ] CMake 已启用 `ALGOLIB_WITH_ONNXRUNTIME=ON`。
- [ ] exe 同目录存在 ONNX Runtime 1.29 的 DLL。
- [ ] 算法包已经注册并激活。
- [ ] 请求输入符合算法包的 `input.schema.json`。
- [ ] 调 Python 算法前，对应服务已经启动且 `/health` 正常。
- [ ] Python 服务端口和 Algorithm Card 一致。

更完整的算法清单、端口表、服务契约和生产接入说明，参见 `algorithm_library_cpp_backend_usage_guide.md`。
