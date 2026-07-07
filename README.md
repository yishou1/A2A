# Algorithm Library

这是一个面向 Agent 调用的本地算法库原型系统。项目用 C++17 实现，核心目标是把不同后端形式的算法统一注册到算法库中，再通过 CLI 或 HTTP Server 对外提供查询、管理和执行能力。

当前支持两类算法后端：

- `onnx`：本地 ONNX 模型文件。
- `python_http_service`：外部 Python HTTP 推理服务。

系统不负责训练模型，也不做自动后端选择、自动 fallback、Pipeline 动态编排、gRPC、嵌入式 Python、TensorRT、OpenVINO、Triton 或 LibTorch 集成。

## 系统在做什么

这个项目把一个算法接入过程拆成三件事：

1. **用 Algorithm Card 描述算法**

   每个算法包需要提供 `algorithm_card.yaml`，里面描述算法身份、版本、后端类型、输入输出 schema、运行时配置、预处理配置、后处理配置、Agent 可读说明、性能信息和安全约束。

2. **把算法注册到 Algorithm Registry**

   注册表用唯一键管理算法：

   ```text
   algorithm_id + version + backend_type
   ```

   注册后可以执行：

   ```text
   validate
   activate
   disable
   delete
   list
   show-card
   ```

3. **通过统一入口执行算法**

   执行请求统一使用 `AlgorithmRequest`，执行结果统一使用 `AlgorithmResult`。底层根据 `backend_type` 调用不同 runner：

   ```text
   onnx -> OnnxRunner
   python_http_service -> PythonHttpRunner
   ```

## 目录结构

```text
include/
  algolib/
    core/        核心数据模型，例如 AlgorithmCard、AlgorithmKey、Status、ErrorCode
    registry/    算法注册表接口
    runtime/     统一执行请求、执行结果、runner 接口和运行时协调器
    validation/  算法卡片、ONNX 包、Python Service 包校验接口
    io/          JSON、YAML、文件、HTTP Client、SHA256 等工具接口
    server/      HTTP Server 公共接口

src/
  cli/          CLI 程序入口，生成 algolib.exe
  server/       HTTP Server 程序入口，生成 algolib_server.exe
  core/         核心模型实现
  registry/     注册表持久化和生命周期管理实现
  runtime/      ONNX、Python HTTP Service 和统一执行逻辑
  validation/   算法包校验逻辑
  io/           基础 IO 工具实现

examples/
  onnx_text_classifier/                  ONNX 示例算法包
  python_http_service_llm_explainer/     Python HTTP Service 示例算法包

tests/
  注册表、schema、ONNX、Python Service、HTTP Server 测试

tools/
  辅助脚本，例如生成示例 ONNX 文件、VS Code MSVC shell 启动脚本
```

## 构建要求

需要：

- CMake 3.20+
- C++17 编译器
- Windows 下推荐 Visual Studio 2022 Build Tools

默认构建不启用真实 ONNX Runtime，而是使用项目内的 stub 路径，便于开发和测试。

## 构建方式

在项目根目录执行：

```powershell
cmake -S . -B build
cmake --build build
```

如果修改了头文件中的结构体字段，建议做一次干净构建：

```powershell
cmake --build build --clean-first
```

构建后会生成：

```text
build/algolib.exe
build/algolib_server.exe
build/algolib_tests.exe
```

## 运行测试

```powershell
ctest --test-dir build --output-on-failure
```

如果测试通过，会看到：

```text
100% tests passed
```

## CLI 使用方式

CLI 程序是：

```powershell
.\build\algolib.exe
```

它是短生命周期工具：执行一次命令，输出 JSON，然后退出。

### 查看算法列表

```powershell
.\build\algolib.exe list
```

如果注册表为空，会输出：

```json
[]
```

### 注册 ONNX 示例算法

```powershell
.\build\algolib.exe register .\examples\onnx_text_classifier\1.0.0
```

### 激活算法

```powershell
.\build\algolib.exe activate onnx_text_classifier 1.0.0 onnx
```

只有 `active` 状态的算法可以被执行。

### 查看算法卡片

```powershell
.\build\algolib.exe show-card onnx_text_classifier 1.0.0 onnx
```

输出中包含：

- `entry`：完整注册表条目。
- `agent_view`：面向 Agent 的简化可读视图。

### 执行算法

先准备一个请求文件，例如 `request.json`：

```json
{
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "onnx_text_classifier",
  "version": "1.0.0",
  "backend_type": "onnx",
  "inputs": {
    "text": "Classify this task text."
  }
}
```

然后执行：

```powershell
.\build\algolib.exe run request.json
```

## HTTP Server 使用方式

HTTP Server 程序是：

```powershell
.\build\algolib_server.exe
```

它是常驻服务：启动后会一直监听端口，等待 Agent、curl 或外部系统通过 HTTP 调用。

HTTP Server 会缓存已加载的 ONNX runner/session。第一次 `/run` 会加载模型，后续同一个 `algorithm_id + version + backend_type` 的请求会复用缓存，避免每次请求都重复加载 ONNX 模型。

缓存会在 `/reload`、注册、校验、激活、禁用或删除算法后清理或失效。可以通过 `/health` 查看当前缓存数量：

```json
{
  "ok": true,
  "runner_cache_size": 1
}
```

### 启动服务

默认启动：

```powershell
.\build\algolib_server.exe
```

等价于：

```powershell
.\build\algolib_server.exe --host 127.0.0.1 --port 8088
```

指定注册表路径：

```powershell
.\build\algolib_server.exe --host 127.0.0.1 --port 8088 --registry .\.algolib\registry.json
```

如果需要局域网访问，可以使用：

```powershell
.\build\algolib_server.exe --host 0.0.0.0 --port 8088
```

注意：局域网或公网暴露前应增加鉴权、访问控制和限流。

### 健康检查

新开一个终端执行：

```powershell
curl http://127.0.0.1:8088/health
```

正常响应示例：

```json
{
  "ok": true,
  "status": "ready",
  "registry_path": ".algolib/registry.json",
  "execution_log_path": ""
}
```

### 查询算法列表

```powershell
curl http://127.0.0.1:8088/algorithms
```

默认只返回 `active` 算法。查看非 deleted 的所有算法：

```powershell
curl "http://127.0.0.1:8088/algorithms?active_only=false"
```

### HTTP 注册算法

```powershell
curl -X POST http://127.0.0.1:8088/algorithms/register `
  -H "Content-Type: application/json" `
  -d "{\"package_or_card_path\":\"examples/onnx_text_classifier/1.0.0\"}"
```

### HTTP 激活算法

```powershell
curl -X POST http://127.0.0.1:8088/algorithms/onnx_text_classifier/1.0.0/onnx/activate
```

### HTTP 执行算法

```powershell
curl -X POST http://127.0.0.1:8088/run `
  -H "Content-Type: application/json" `
  -d "@request.json"
```

## HTTP API 列表

```text
GET    /health
POST   /reload
GET    /algorithms
GET    /algorithms?active_only=false
GET    /algorithms/<algorithm_id>/<version>/<backend_type>
POST   /algorithms/register
POST   /algorithms/<algorithm_id>/<version>/<backend_type>/validate
POST   /algorithms/<algorithm_id>/<version>/<backend_type>/activate
POST   /algorithms/<algorithm_id>/<version>/<backend_type>/disable
DELETE /algorithms/<algorithm_id>/<version>/<backend_type>
POST   /run
```

## 接入 ONNX 算法

ONNX 算法包至少需要包含：

```text
algorithm_card.yaml
model.onnx
input.schema.json
output.schema.json
preprocess.yaml
postprocess.yaml
```

可选文件：

```text
tensor_contract.yaml
tokenizer.json
label_map.json
golden_cases/
```

当前 ONNX 路径不是“任意 model.onnx 零改动接入”。模型输入输出 tensor 名称、类型、形状需要和 `preprocess.yaml` / `postprocess.yaml` 中声明的适配器契约匹配。

当前支持的 preprocess 类型：

```text
no_op
tensor_from_json
text_tokenization
json_to_tensor_map
```

当前支持的 postprocess 类型：

```text
no_op
classification_postprocess
raw_tensor_to_json
```

### tensor_contract.yaml

`tensor_contract.yaml` 用来显式声明 ONNX 模型真实的 tensor 输入输出签名：

```yaml
inputs:
  - name: features
    dtype: float32
    shape: [1, 3]
outputs:
  - name: scores
    dtype: float32
    shape: [1, 3]
```

在 `algorithm_card.yaml` 中引用：

```yaml
machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json
  tensor_contract_ref: tensor_contract.yaml
```

### json_to_tensor_map

`json_to_tensor_map` 是通用预处理适配器，用来把业务 JSON 字段映射成一个或多个 ONNX tensor：

```yaml
type: json_to_tensor_map
mappings:
  - json_path: $.features
    tensor_name: features
```

如果 `tensor_contract.yaml` 中已经声明了 `features` 的 dtype 和 shape，这里可以不用重复写。也可以在 mapping 中显式覆盖：

```yaml
type: json_to_tensor_map
mappings:
  - json_path: $.features
    tensor_name: features
    dtype: float32
    shape: [1, 3]
```

### raw_tensor_to_json

`raw_tensor_to_json` 是通用后处理适配器，用来把 ONNX 输出 tensor 原样放到 JSON 输出中：

```yaml
type: raw_tensor_to_json
outputs:
  - tensor_name: scores
    json_path: $.scores
```

如果模型输出 tensor 是 `[0.1, 0.7, 0.2]`，最终输出就是：

```json
{
  "scores": [0.1, 0.7, 0.2]
}
```

## 接入 Python HTTP Service 算法

Python HTTP Service 算法包至少需要：

```text
algorithm_card.yaml
input.schema.json
output.schema.json
```

`algorithm_card.yaml` 中需要配置：

```yaml
machine_spec:
  runtime:
    backend_type: python_http_service
    endpoint: http://127.0.0.1:9000/predict
    health_endpoint: http://127.0.0.1:9000/health
    metadata_endpoint: http://127.0.0.1:9000/metadata
    timeout_ms: 3000
```

调用时，算法库会先通过 `PythonHttpRunner` 请求外部 Python 服务，再把结果统一转换成 `AlgorithmResult`。

## Agent 调用方式

当前系统已经提供两种对外入口：

```text
CLI:
  algolib.exe list
  algolib.exe show-card
  algolib.exe run

HTTP Server:
  GET  /algorithms
  GET  /algorithms/<id>/<version>/<backend>
  POST /run
```

后续如果要接入 Agent 框架，推荐在 HTTP Server 之上再封装 MCP Server，把 HTTP API 转成标准工具：

```text
algolib.list_algorithms
algolib.show_card
algolib.run_algorithm
```

## 常见问题

### curl 无法连接到 127.0.0.1:8088

说明 HTTP Server 没有启动，先运行：

```powershell
.\build\algolib_server.exe --host 127.0.0.1 --port 8088
```

### /algorithms 返回空数组

可能是当前没有 `active` 状态算法。先注册并激活：

```powershell
.\build\algolib.exe register .\examples\onnx_text_classifier\1.0.0
.\build\algolib.exe activate onnx_text_classifier 1.0.0 onnx
```

### Windows 阻止运行 exe

如果看到 “Application Control policy has blocked this file”，说明 Windows Code Integrity、WDAC 或 Smart App Control 拦截了未签名的本地编译产物。可以选择：

- 让管理员放行构建目录。
- 使用受信任证书签名 exe。
- 调整本机应用控制策略。
- 在 WSL/Linux 环境中构建运行。

## GitHub 提交建议

提交源码前建议确认：

```powershell
git status
```

不要提交：

```text
build/
build-*/
.algolib/
*.exe
*.pdb
weekly_report_*.md
```

这些已经在 `.gitignore` 中忽略。
