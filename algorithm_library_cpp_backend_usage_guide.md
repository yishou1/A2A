# 算法库现状报告与 C++ 双后端调用指南

生成日期：2026-08-14；环境状态更新：2026-08-16
适用分支：`lkf/algorithmrepo`（已合并 `origin/zsl/algorithmrepo`）

## 1. 结论

当前本地项目已经同时具备两种后端：

| 后端 | 本地内容 | 当前机器运行状态 | 结论 |
|---|---|---|---|
| ONNX | 4 个算法包均包含真实二进制 `model.onnx` | ONNX Runtime 1.29.0 SDK 已安装，C++ 真实推理已验证 | 可以由 C++ 进程内直接调用 |
| Python HTTP Service | 26 张算法卡；其中 25 个算法有本地 Python 实现，1 个只有接口示例 | `algolib` Conda 环境已安装完整运行与测试依赖 | C++ 通过 HTTP 调用 |

这里的“真实”需要分成两层理解：

1. **运行链路是真实的**：ONNX 使用真实 ONNX Runtime；Python Service 使用真实 HTTP 请求，不是 C++ 内部伪造返回。
2. **不等于生产模型已完备**：3 个 ONNX 模型标记为 bootstrap 模型，另 1 个是最小演示模型；部分 Python 算法支持规则、轻量回退或 mock 模式，不能直接等同于生产训练模型。

## 2. 统一调用架构

```text
C++ 业务代码
    |
    | AlgorithmRequest
    v
ExecutionCoordinator
    |
    +--> AlgorithmRegistry：查找算法、检查 active 状态、校验输入输出 Schema
    |
    +--> RuntimeFactory
            |
            +--> OnnxRunner
            |      preprocess -> ONNX Runtime -> postprocess
            |
            +--> PythonHttpRunner
                   GET /health -> POST /predict
    |
    v
AlgorithmResult + execution_audit.jsonl
```

两种后端共用以下 C++ 对象：

- `AlgorithmRegistry`：注册、校验、激活和查询算法。
- `AlgorithmRequest`：统一请求结构。
- `ExecutionCoordinator`：统一执行入口。
- `AlgorithmResult`：统一返回结构。
- `RuntimeRunnerCache`：复用 ONNX Session，避免每次请求重新加载模型。

真正决定后端的是三元键：

```text
algorithm_id + version + backend_type
```

## 3. 当前 ONNX 算法

| algorithm_id | 模型输入 | 模型性质 | 模型文件 |
|---|---|---|---|
| `compliance_risk_scorer_onnx` | `[1, 6]` 合规特征 | bootstrap Logistic Regression | `examples/compliance_risk_scorer_onnx/1.0.0/model.onnx` |
| `decision_plan_recommender_onnx` | `[1, 8]` 方案特征 | bootstrap Logistic Regression | `examples/decision_plan_recommender_onnx/1.0.0/model.onnx` |
| `target_trend_predictor_onnx` | `[1, 12, 4]` 时序特征 | bootstrap 固定窗口趋势模型 | `examples/target_trend_predictor_onnx/1.0.0/model.onnx` |
| `onnx_text_classifier` | 文本预处理后的张量 | 最小 ONNX 演示模型，固定三类输出 | `examples/onnx_text_classifier/1.0.0/model.onnx` |

前三个算法适合验证决策类特征推理链路，但其元数据明确说明不是生产数据训练模型。`onnx_text_classifier` 用于验证注册、预处理、推理和后处理流程，不应当作为真实文本分类能力评估。

## 4. 当前 Python Service 算法

### 4.1 独立服务实现

| 默认端口 | algorithm_id |
|---:|---|
| 9010 | `execution_rule_matcher` |
| 9011 | `trajectory_linear_predictor` |
| 9012 | `execution_control_planner` |
| 9013 | `mission_feature_adapter` |
| 9014 | `mission_completion_scorer` |
| 9015 | `closed_loop_decision_advisor` |
| 9016 | `xbd_damage_assessor` |
| 9020 | `decision_planning_core` |
| 9021 | `compliance_authorization_core` |
| 9020 | `battlefield_rtdetr_detector` |
| 9021 | `siamese_mask2former_damage` |
| 9022 | `edl_evidential_verifier` |
| 9023 | `motr_neural_kalman_tracker` |
| 9024 | `marl_ppo_task_scheduler` |
| 9025 | `imagebind_multimodal_encoder` |
| 9026 | `multimodal_mamba_fusion` |
| 9027 | `supcon_meta_classifier` |
| 9028 | `synapse_rag_retriever` |
| 9029 | `knowledge_semantic_comm` |
| 9030 | `marl_dynamic_router` |

### 4.2 聚合服务实现

以下 5 个算法由 `services/track_threat_algorithms/app/main.py` 统一启动，默认监听 9022：

| algorithm_id | predict 路径 |
|---|---|
| `multimodal_feature_fuser` | `/multimodal_feature_fuser/predict` |
| `target_type_classifier` | `/target_type_classifier/predict` |
| `track_state_updater` | `/track_state_updater/predict` |
| `trajectory_predictor` | `/trajectory_predictor/predict` |
| `graph_relation_reasoner` | `/graph_relation_reasoner/predict` |

补充运行边界：

- `decision_planning_core` 和 `compliance_authorization_core` 会导入外部 A2A 仓库中的 `decision_agents`。当前工作区没有该 A2A 目录，因此这两个服务入口虽已存在，但还需要配置 `A2A_REPO_ROOT` 并提供对应仓库。
- TIA 类服务可能依赖额外模型权重。批量启动脚本会设置 `TIA_USE_MOCK=1`，适合接口联调，不代表真实权重已经加载。

### 4.3 只有接口示例

`llm_rule_explainer` 有完整 Algorithm Card、Schema 和服务协议，默认地址为 `http://localhost:8080`，但当前仓库没有对应的 Python 服务程序。使用前需要自行实现满足 `/health`、`/metadata` 和 `/predict` 契约的服务。

### 4.4 默认端口冲突

以下服务不能按默认端口同时启动：

- `decision_planning_core` 与 `battlefield_rtdetr_detector` 都使用 9020。
- `compliance_authorization_core` 与 `siamese_mask2former_damage` 都使用 9021。
- `edl_evidential_verifier` 与 `track_threat_algorithms` 聚合服务都使用 9022。

修改端口时必须同时修改两处：

1. 启动服务前设置 `PORT` 环境变量。
2. 修改对应 `examples/<algorithm_id>/1.0.0/algorithm_card.yaml` 中的 `endpoint`、`health_endpoint` 和 `metadata_endpoint`。

## 5. 构建支持真实 ONNX Runtime 的 C++ 算法库

当前机器的 SDK 路径为：

```text
D:\Desktop\algorithm repo1\build-deps\onnxruntime-win-x64-1.29.0
```

在 Visual Studio Developer PowerShell 中执行：

```powershell
$OrtRoot = "D:\Desktop\algorithm repo1\build-deps\onnxruntime-win-x64-1.29.0"

cmake -S . -B build-ort -G Ninja `
  -DALGOLIB_WITH_ONNXRUNTIME=ON `
  -DALGOLIB_ONNXRUNTIME_ROOT="$OrtRoot"

cmake --build build-ort --parallel
ctest --test-dir build-ort --output-on-failure
```

必须满足：

- `ALGOLIB_WITH_ONNXRUNTIME=ON`。
- SDK 中存在 `include/onnxruntime_cxx_api.h`、`lib/onnxruntime.lib` 和 `lib/onnxruntime.dll`。
- `onnxruntime.dll` 位于最终可执行文件旁边，或其目录已加入 `PATH`。

运行结果中的下面字段是判断真实推理是否启用的最终依据：

```json
"session_backend": "onnxruntime"
```

如果显示 `"stub"`，说明程序不是用真实 ONNX Runtime 构建的。

## 6. CMake 中接入算法库

如果 C++ 调用程序放在当前项目中，可以在根 `CMakeLists.txt` 的 `algolib` target 定义之后增加：

```cmake
add_executable(cpp_backend_demo cpp_backend_demo.cpp)
target_link_libraries(cpp_backend_demo PRIVATE algolib)
algolib_copy_onnxruntime_dll_if_needed(cpp_backend_demo)
```

如果调用程序是另一个 CMake 项目，可使用：

```cmake
set(ALGOLIB_WITH_ONNXRUNTIME ON CACHE BOOL "" FORCE)
set(ALGOLIB_ONNXRUNTIME_ROOT
    "D:/Desktop/algorithm repo1/build-deps/onnxruntime-win-x64-1.29.0"
    CACHE PATH "" FORCE)

add_subdirectory(
    "D:/Desktop/algorithm repo1"
    "${CMAKE_BINARY_DIR}/algolib-src"
)

add_executable(cpp_backend_demo cpp_backend_demo.cpp)
target_link_libraries(cpp_backend_demo PRIVATE algolib)
```

外部项目还需要保证 `onnxruntime.dll` 被复制到 `cpp_backend_demo.exe` 同目录。

## 7. 通用 C++ 调用代码

下面代码同时适用于两种后端，差异只有包路径、`BackendType` 和 `inputs`。

```cpp
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>

#include "algolib/core/error_code.h"
#include "algolib/registry/algorithm_registry.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"
#include "algolib/runtime/execution_coordinator.h"
#include "algolib/runtime/runtime_runner_cache.h"

namespace fs = std::filesystem;
using namespace algolib;

void RequireOk(const Status& status, const std::string& action) {
    if (!status.ok()) {
        throw std::runtime_error(action + ": " + status.ToString());
    }
}

template <typename T>
void RequireOk(const Result<T>& result, const std::string& action) {
    if (!result.ok()) {
        throw std::runtime_error(action + ": " + result.status().ToString());
    }
}

void EnsureActive(AlgorithmRegistry& registry,
                  const AlgorithmKey& key,
                  const fs::path& package_path) {
    auto existing = registry.Get(key);
    if (!existing.ok()) {
        if (existing.status().code() != ErrorCode::kAlgorithmNotFound) {
            RequireOk(existing, "query algorithm");
        }
        RequireOk(registry.Register(package_path), "register algorithm");
    }

    // Activate 对 validated、disabled 和 active 状态均可安全调用。
    RequireOk(registry.Activate(key), "activate algorithm");
}

AlgorithmResult RunAlgorithm(AlgorithmRegistry& registry,
                             RuntimeRunnerCache& cache,
                             const AlgorithmKey& key,
                             nlohmann::json inputs) {
    AlgorithmRequest request;
    request.algorithm_id = key.algorithm_id;
    request.version = key.version;
    request.backend_type = key.backend_type;
    request.inputs = std::move(inputs);
    request.params = nlohmann::json::object();

    // request_id 和 trace_id 为空时，ExecutionCoordinator 会自动生成。
    ExecutionCoordinator coordinator(
        registry,
        ".algolib/cpp_demo_audit.jsonl",
        &cache);
    return coordinator.Run(request);
}

int main(int argc, char* argv[]) {
    try {
        const bool use_python = argc > 1 && std::string(argv[1]) == "python";

        AlgorithmRegistry registry(".algolib/cpp_demo_registry.json");
        RequireOk(registry.Reload(), "reload registry");
        RuntimeRunnerCache cache;

        AlgorithmKey key;
        fs::path package_path;
        nlohmann::json inputs;

        if (!use_python) {
            key = {
                "compliance_risk_scorer_onnx",
                "1.0.0",
                BackendType::kOnnx,
            };
            package_path = "examples/compliance_risk_scorer_onnx/1.0.0";
            inputs = {
                {"features", {{0.0, 1.0, 0.2, 0.0, 2.0, 0.0}}},
            };
        } else {
            key = {
                "execution_rule_matcher",
                "1.0.0",
                BackendType::kPythonHttpService,
            };
            package_path = "examples/execution_rule_matcher/1.0.0";
            inputs = {
                {"phase", "strike"},
                {"situation",
                 {
                     {"threat_score", 0.75},
                     {"intel_confidence", 0.82},
                     {"resource_readiness", 0.81},
                     {"communication_quality", 0.90},
                 }},
            };
        }

        EnsureActive(registry, key, package_path);
        const AlgorithmResult result = RunAlgorithm(registry, cache, key, inputs);
        std::cout << ToJson(result).dump(2) << '\n';
        return result.ok ? 0 : 1;
    } catch (const std::exception& ex) {
        std::cerr << ex.what() << '\n';
        return 1;
    }
}
```

从仓库根目录运行：

```powershell
# ONNX 后端
.\build-ort\cpp_backend_demo.exe

# Python Service 后端；必须先启动 execution_rule_matcher
.\build-ort\cpp_backend_demo.exe python
```

## 8. ONNX 后端调用细节

ONNX 算法包至少需要：

```text
algorithm_card.yaml
input.schema.json
output.schema.json
model.onnx
tensor_contract.yaml
preprocess.yaml
postprocess.yaml
golden_cases/
```

调用流程：

1. `registry.Register(package_path)` 解析 Algorithm Card 并校验包结构。
2. `registry.Activate(key)` 把算法状态切换为 `active`。
3. `ExecutionCoordinator::Run()` 校验 JSON 输入。
4. `OnnxRunner` 根据 `preprocess.yaml` 生成张量。
5. ONNX Runtime 执行 `model.onnx`。
6. `postprocess.yaml` 把输出张量转换成 JSON。

本机已经实际验证 `compliance_risk_scorer_onnx`，返回结果为：

```json
{
  "ok": true,
  "outputs": {
    "risk_probability": [[0.3941263258457184]]
  },
  "usage": {
    "execution_provider": "cpu",
    "session_backend": "onnxruntime"
  }
}
```

模型路径不是写死的绝对路径。实际路径由以下配置相对于算法包根目录解析：

```yaml
machine_spec:
  runtime:
    backend_type: onnx
    model_uri: model.onnx
```

更换模型时，除 `model_uri` 外，还必须同步检查：

- `tensor_contract.yaml` 中的张量名称、dtype 和 shape。
- `preprocess.yaml` 中的 JSON 到张量映射。
- `postprocess.yaml` 中的张量到 JSON 映射。
- `input.schema.json` 和 `output.schema.json`。
- `golden_cases` 中的预期结果。

## 9. Python Service 后端调用细节

### 9.1 安装和启动示例服务

当前机器已经创建专用 Conda 环境：

```powershell
conda activate algolib
```

该环境使用 Python 3.11，并已安装本项目的完整运行依赖、`pytest` 和 `httpx`。其中 `scikit-learn` 固定为模型生成时使用的 1.7.2。

项目提供的完整依赖安装方式：

```powershell
python -m pip install -r services\requirements.txt
```

这个依赖文件包含 PyTorch、Transformers、Ultralytics 等较重组件。如果只验证 `execution_rule_matcher`，可先安装基础依赖：

```powershell
python -m pip install fastapi uvicorn pydantic numpy pillow
```

启动服务：

```powershell
python services\execution_rule_matcher\app\main.py
```

服务默认提供：

```text
GET  http://127.0.0.1:9010/health
GET  http://127.0.0.1:9010/metadata
POST http://127.0.0.1:9010/predict
```

如果重新创建环境，再执行上述依赖安装命令即可。

### 9.2 C++ 为什么要求先启动服务

`AlgorithmRegistry::Register()` 不只是保存卡片。对 `python_http_service`，注册阶段会访问服务的健康、元数据和 golden case 接口。因此正确顺序是：

```text
安装依赖 -> 启动 Python Service -> Register -> Activate -> Run
```

如果先注册后启动，注册会返回 `SERVICE_UNAVAILABLE`、`SERVICE_NOT_READY` 或相关 HTTP 错误。

### 9.3 Python Service 必须遵守的响应契约

`GET /health` 至少返回：

```json
{
  "ok": true,
  "status": "ready",
  "algorithm_id": "execution_rule_matcher",
  "version": "1.0.0",
  "model_loaded": true
}
```

`POST /predict` 成功时至少返回：

```json
{
  "ok": true,
  "request_id": "...",
  "trace_id": "...",
  "algorithm_id": "execution_rule_matcher",
  "version": "1.0.0",
  "outputs": {},
  "usage": {
    "latency_ms": 1.0
  },
  "error": null
}
```

C++ 的 `PythonHttpRunner` 会校验 HTTP 状态码、算法 ID、版本、`ok` 和 `outputs`，然后再用算法包的 `output.schema.json` 校验输出。

## 10. 命令行诊断方式

在接入自己的 C++ 业务代码前，建议先用同一套 C++ 库生成的 CLI 验证算法包。

### 10.1 ONNX

```powershell
$env:ALGOLIB_REGISTRY_PATH = "$PWD\runtime\registry.json"

.\build-ort\algolib.exe register `
  .\examples\compliance_risk_scorer_onnx\1.0.0

.\build-ort\algolib.exe activate `
  compliance_risk_scorer_onnx 1.0.0 onnx

.\build-ort\algolib.exe run .\request_onnx.json
```

### 10.2 Python Service

先启动服务，再执行：

```powershell
$env:ALGOLIB_REGISTRY_PATH = "$PWD\runtime\registry.json"

.\build-ort\algolib.exe register `
  .\examples\execution_rule_matcher\1.0.0

.\build-ort\algolib.exe activate `
  execution_rule_matcher 1.0.0 python_http_service

.\build-ort\algolib.exe run `
  .\examples\execution_rule_matcher\1.0.0\golden_cases\case_001_request.json
```

## 11. 常见问题

### `ALGORITHM_NOT_ACTIVE`

算法已经注册但没有激活。调用：

```cpp
registry.Activate(key);
```

### `REGISTRY_CONFLICT`

同一个 `algorithm_id + version + backend_type` 被重复注册。长期运行的程序应先 `Get()`，不存在时再 `Register()`，不要每次请求都重新注册。

### `SERVICE_UNAVAILABLE` 或 `SERVICE_NOT_READY`

检查：

- Python 服务是否已启动。
- Algorithm Card 中三个 URL 是否与实际端口一致。
- `/health` 是否返回 `model_loaded: true`。
- 是否遇到 9020、9021、9022 默认端口冲突。

### ONNX DLL 无法加载

确认 `onnxruntime.dll` 与 C++ 可执行文件位于同一目录，或将 SDK 的 `lib` 目录加入 `PATH`。

本机 `C:\Windows\System32` 中另有 ONNX Runtime 1.17，而当前 C++ 头文件/API 来自 1.29。只修改 `PATH` 仍可能被系统 DLL 抢先加载，最稳妥的方式是把 SDK 1.29 的 `onnxruntime.dll` 直接复制到 exe 同目录。

### `ONNX_INPUT_TENSOR_MISMATCH` / `ONNX_OUTPUT_TENSOR_MISMATCH`

模型的真实张量名称与 `tensor_contract.yaml`、`preprocess.yaml` 或 `postprocess.yaml` 不一致。替换模型文件时不能只改文件名。

### 输入或输出 Schema 校验失败

传给 `AlgorithmRequest::inputs` 的内容必须是业务输入本身，不要再嵌套一层完整请求。完整请求信封由 `AlgorithmRequest` 的其他字段承载。

## 12. 上线前建议

1. 将 bootstrap/演示 ONNX 模型替换为经过正式数据训练和评估的模型。
2. 为所有 Python Service 固化虚拟环境或容器镜像，不要共用不确定的系统 Python。
3. 统一重新规划服务端口，消除 9020、9021、9022 冲突。
4. 不要用 `scripts/start_a2a_algorithm_services.ps1` 中默认设置的 `TIA_USE_MOCK=1` 作为生产配置。
5. 每次替换模型后执行 golden case、C++ 单元测试和算法包验收。
6. 生产进程中复用 `RuntimeRunnerCache`，避免 ONNX 模型反复加载。
7. 持久化并监控 `execution_audit.jsonl`，记录请求 ID、链路 ID、耗时和错误。

## 13. 当前验证记录

- 真实 ONNX Runtime C++ 构建：通过。
- C++ 单元测试：100% 通过。
- 30 个算法包静态验收：30 通过，0 失败。
- `compliance_risk_scorer_onnx` 注册、激活、真实推理：通过。
- Python Service 源码与算法实现：已存在。
- `algolib` Conda 环境：Python 3.11.15，运行与测试依赖已安装。
- 不依赖外部 A2A 源码的 Python 测试：44 通过、0 失败，另有 3 个子测试通过。
- 完整 Python 测试仍需要外部 `D:\Desktop\A2A` 中的 `decision_agents` 和 `a2a_protocol` 模块。
