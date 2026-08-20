# C++ 通过 HTTP Server 调用算法库：Windows 与 Linux 示例

本文只介绍一种接入方式：**外部 C++ 程序通过 HTTP 调用 `algolib_server`**。

无论算法后端是 ONNX 还是 Python Service，C++ 客户端始终调用同一个接口：

```text
POST http://127.0.0.1:8088/run
```

## 1. 调用结构

```text
C++ HTTP 客户端
        |
        | POST /run + JSON
        v
algolib_server（默认 8088）
        |
        +-- backend_type = onnx
        |       -> OnnxRunner
        |       -> ONNX Runtime
        |       -> model.onnx
        |
        +-- backend_type = python_http_service
                -> PythonHttpRunner
                -> Python Service（本例 9010）
                -> /health、/predict
```

关键点：

- C++ 客户端不直接链接 `algolib`，也不直接加载 ONNX Runtime。
- `algolib_server` 负责注册表、输入输出校验、后端选择和运行器缓存。
- ONNX Runtime DLL 或 `.so` 只需要配置在服务器一侧。
- Python 后端必须先启动对应的 Python Service。
- Windows 和 Linux 使用同一份 C++ HTTP 客户端源码和同一种请求 JSON。

## 2. 本项目提供的完整样例

```text
examples/cpp_http_client/main.cpp
examples/cpp_http_client/requests/onnx_request.json
examples/cpp_http_client/requests/python_service_request.json
```

编译后生成：

```text
Windows：algorithm_http_client.exe
Linux：  algorithm_http_client
```

调用格式：

```text
algorithm_http_client <请求 JSON> [服务器地址] [服务器端口]
```

省略地址和端口时默认使用：

```text
127.0.0.1:8088
```

## 3. C++ HTTP 客户端代码

客户端只做四件事：读取请求 JSON、连接 8088、调用 `/run`、打印结果。

```cpp
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

#include <httplib.h>
#include <nlohmann/json.hpp>

// 从磁盘读取完整 AlgorithmRequest JSON。
nlohmann::json ReadRequest(const std::string& request_path) {
    std::ifstream input(request_path, std::ios::in | std::ios::binary);
    if (!input.is_open()) {
        throw std::runtime_error("无法打开请求文件：" + request_path);
    }

    nlohmann::json request;
    input >> request;
    return request;
}

int main(int argc, char* argv[]) {
    if (argc < 2 || argc > 4) {
        std::cerr
            << "用法：algorithm_http_client "
               "<request_json> [server_host] [server_port]\n";
        return 1;
    }

    try {
        const std::string request_path = argv[1];
        const std::string server_host = argc >= 3 ? argv[2] : "127.0.0.1";
        const int server_port = argc >= 4 ? std::stoi(argv[3]) : 8088;

        const nlohmann::json request = ReadRequest(request_path);

        // 连接算法库统一 HTTP Server。
        httplib::Client client(server_host, server_port);
        client.set_connection_timeout(3, 0);
        client.set_read_timeout(30, 0);

        // ONNX 和 Python Service 都调用同一个 /run。
        const auto response = client.Post(
            "/run",
            request.dump(),
            "application/json");

        if (!response) {
            std::cerr << "无法连接算法库服务\n";
            return 2;
        }

        std::cout << "HTTP " << response->status << '\n';
        const auto result = nlohmann::json::parse(response->body);
        std::cout << result.dump(2) << '\n';

        return response->status >= 200 &&
                       response->status < 300 &&
                       result.value("ok", false)
                   ? 0
                   : 3;
    } catch (const std::exception& exception) {
        std::cerr << "客户端错误：" << exception.what() << '\n';
        return 1;
    }
}
```

项目中的完整、带非 JSON 响应保护的版本见 `examples/cpp_http_client/main.cpp`。

CMake 配置：

```cmake
add_executable(
    algorithm_http_client
    examples/cpp_http_client/main.cpp
)

target_link_libraries(
    algorithm_http_client
    PRIVATE
        nlohmann_json::nlohmann_json
        cpp_httplib_interface
)
```

客户端没有链接 `algolib`，因此客户端本身不需要 `onnxruntime.dll` 或 `libonnxruntime.so`。

## 4. 统一请求和返回格式

### 4.1 ONNX 请求

文件：`examples/cpp_http_client/requests/onnx_request.json`

```json
{
  "request_id": "http-cpp-onnx-001",
  "trace_id": "http-cpp-demo-001",
  "algorithm_id": "compliance_risk_scorer_onnx",
  "version": "1.0.0",
  "backend_type": "onnx",
  "inputs": {
    "features": [[0.0, 1.0, 0.2, 0.0, 2.0, 0.0]]
  },
  "params": {}
}
```

成功结果的关键字段：

```json
{
  "ok": true,
  "backend_type": "onnx",
  "outputs": {
    "risk_probability": [[0.3941263258457184]]
  },
  "usage": {
    "execution_provider": "cpu",
    "session_backend": "onnxruntime"
  }
}
```

`session_backend: onnxruntime` 表示服务器使用了真实 ONNX Runtime。

### 4.2 Python Service 请求

文件：`examples/cpp_http_client/requests/python_service_request.json`

```json
{
  "request_id": "http-cpp-python-001",
  "trace_id": "http-cpp-demo-002",
  "algorithm_id": "execution_rule_matcher",
  "version": "1.0.0",
  "backend_type": "python_http_service",
  "inputs": {
    "phase": "strike",
    "situation": {
      "threat_score": 0.75,
      "intel_confidence": 0.82,
      "resource_readiness": 0.81,
      "communication_quality": 0.9
    }
  },
  "params": {}
}
```

成功结果的关键字段：

```json
{
  "ok": true,
  "backend_type": "python_http_service",
  "outputs": {
    "matched_items": [
      "intel=good",
      "phase=strike",
      "resource=ready",
      "threat=high"
    ],
    "primary_rule": {
      "rule_id": "RULE-003"
    }
  }
}
```

## 5. Windows 完整演示

当前机器已有可用的 C++ 构建目录：

```text
build-zsl-verify
```

如果修改过 C++ 客户端，先在 Visual Studio Developer PowerShell 中编译：

```powershell
cd "D:\Desktop\algorithm repo1"
cmake --build build-zsl-verify --target algorithm_http_client --parallel
```

### 5.1 启动算法库 HTTP Server

终端 1：

```powershell
cd "D:\Desktop\algorithm repo1"

.\build-zsl-verify\algolib_server.exe `
  --host 127.0.0.1 `
  --port 8088 `
  --registry .\.algolib\http_demo_registry.json
```

保持终端 1 运行。新开终端检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8088/health
```

### 5.2 首次注册并激活 ONNX 算法

终端 2：

```powershell
cd "D:\Desktop\algorithm repo1"

$OnnxPackage = (Resolve-Path `
  ".\examples\compliance_risk_scorer_onnx\1.0.0").Path

$RegisterBody = @{
  package_or_card_path = $OnnxPackage
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8088/algorithms/register" `
  -ContentType "application/json" `
  -Body $RegisterBody

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8088/algorithms/compliance_risk_scorer_onnx/1.0.0/onnx/activate"
```

注册信息会持久化到 `.algolib/http_demo_registry.json`。以后再次演示时跳过注册，只需确认算法处于 active 状态。

### 5.3 C++ 客户端调用 ONNX

```powershell
.\build-zsl-verify\algorithm_http_client.exe `
  .\examples\cpp_http_client\requests\onnx_request.json
```

预期：HTTP 200、`ok: true`、`session_backend: onnxruntime`。

### 5.4 启动 Python Service

终端 3：

```powershell
cd "D:\Desktop\algorithm repo1"
conda activate algolib
python services\execution_rule_matcher\app\main.py
```

保持终端 3 运行。该服务监听 `127.0.0.1:9010`。

### 5.5 首次注册并激活 Python Service 算法

回到终端 2：

```powershell
$PythonPackage = (Resolve-Path `
  ".\examples\execution_rule_matcher\1.0.0").Path

$RegisterBody = @{
  package_or_card_path = $PythonPackage
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8088/algorithms/register" `
  -ContentType "application/json" `
  -Body $RegisterBody

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8088/algorithms/execution_rule_matcher/1.0.0/python_http_service/activate"
```

Python Service 注册校验会访问它的 `/health`、`/metadata` 和 `/predict`，所以必须先启动服务。

### 5.6 C++ 客户端调用 Python Service

```powershell
.\build-zsl-verify\algorithm_http_client.exe `
  .\examples\cpp_http_client\requests\python_service_request.json
```

预期：HTTP 200、`ok: true`、`backend_type: python_http_service`。

演示结束后，分别在终端 1 和终端 3 按 `Ctrl+C` 停止 Server 和 Python Service。

## 6. Linux 完整演示

以下示例假设：

- 仓库位于 `/opt/algorithm-repo`。
- 已安装支持 C++17 的 GCC、CMake 3.20+ 和 Python 3.11。
- Linux x64 ONNX Runtime C++ SDK 1.29 已解压到 `/opt/onnxruntime-linux-x64-1.29.0`。

路径应根据实际机器修改。

### 6.1 创建 Python 环境

```bash
cd /opt/algorithm-repo

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r services/requirements.txt
```

如果使用 Conda，也可以创建 Python 3.11 环境后安装同一个 requirements 文件。

### 6.2 编译 Server 和 C++ HTTP 客户端

```bash
cd /opt/algorithm-repo

export ORT_ROOT=/opt/onnxruntime-linux-x64-1.29.0

cmake -S . -B build-linux \
  -DCMAKE_BUILD_TYPE=Release \
  -DALGOLIB_WITH_ONNXRUNTIME=ON \
  -DALGOLIB_ONNXRUNTIME_ROOT="$ORT_ROOT"

cmake --build build-linux --parallel
ctest --test-dir build-linux --output-on-failure
```

首次 CMake 配置需要取得 nlohmann/json、yaml-cpp 和 cpp-httplib；离线环境需要提前准备依赖缓存或改为本地依赖源。

编译结果：

```text
build-linux/algolib_server
build-linux/algorithm_http_client
```

### 6.3 配置 Linux ONNX Runtime 动态库

Linux Server 需要找到 `libonnxruntime.so`：

```bash
export ORT_ROOT=/opt/onnxruntime-linux-x64-1.29.0
export LD_LIBRARY_PATH="$ORT_ROOT/lib:${LD_LIBRARY_PATH:-}"
```

这只影响服务器进程。`algorithm_http_client` 不直接使用 ONNX Runtime。

### 6.4 启动算法库 HTTP Server

终端 1：

```bash
cd /opt/algorithm-repo
export ORT_ROOT=/opt/onnxruntime-linux-x64-1.29.0
export LD_LIBRARY_PATH="$ORT_ROOT/lib:${LD_LIBRARY_PATH:-}"

./build-linux/algolib_server \
  --host 127.0.0.1 \
  --port 8088 \
  --registry ./.algolib/http_demo_registry.json
```

终端 2 检查：

```bash
curl -sS http://127.0.0.1:8088/health
```

### 6.5 首次注册、激活并调用 ONNX

终端 2：

```bash
cd /opt/algorithm-repo

curl -sS -X POST http://127.0.0.1:8088/algorithms/register \
  -H 'Content-Type: application/json' \
  -d "{\"package_or_card_path\":\"$PWD/examples/compliance_risk_scorer_onnx/1.0.0\"}"

curl -sS -X POST \
  http://127.0.0.1:8088/algorithms/compliance_risk_scorer_onnx/1.0.0/onnx/activate

./build-linux/algorithm_http_client \
  ./examples/cpp_http_client/requests/onnx_request.json
```

以后再次演示时不需要重复注册。

### 6.6 启动 Python Service

终端 3：

```bash
cd /opt/algorithm-repo
source .venv/bin/activate
python services/execution_rule_matcher/app/main.py
```

保持终端 3 运行。

### 6.7 首次注册、激活并调用 Python Service

终端 2：

```bash
cd /opt/algorithm-repo

curl -sS -X POST http://127.0.0.1:8088/algorithms/register \
  -H 'Content-Type: application/json' \
  -d "{\"package_or_card_path\":\"$PWD/examples/execution_rule_matcher/1.0.0\"}"

curl -sS -X POST \
  http://127.0.0.1:8088/algorithms/execution_rule_matcher/1.0.0/python_http_service/activate

./build-linux/algorithm_http_client \
  ./examples/cpp_http_client/requests/python_service_request.json
```

演示结束后，在终端 1 和终端 3 按 `Ctrl+C` 停止两个服务。

## 7. 从其他机器调用

如果 C++ 客户端与 `algolib_server` 不在同一台机器：

1. Server 使用 `--host 0.0.0.0` 监听局域网地址。
2. 客户端把第二、第三个参数改为服务器 IP 和端口。
3. 放行服务器防火墙中的 8088 端口。
4. Python Service 可以继续只监听服务器本机端口，由 `algolib_server` 转发。

客户端示例：

```powershell
.\algorithm_http_client.exe request.json 192.168.1.20 8088
```

```bash
./algorithm_http_client request.json 192.168.1.20 8088
```

当前 Server 没有内置用户鉴权和 TLS。不要直接暴露到公网；局域网部署前也应增加访问控制、认证、TLS 和限流。

## 8. 常用 HTTP 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 检查算法库 Server 是否就绪 |
| GET | `/algorithms` | 查看 active 算法 |
| GET | `/algorithms?active_only=false` | 查看所有非 deleted 算法 |
| POST | `/algorithms/register` | 注册算法包 |
| POST | `/algorithms/<id>/<version>/<backend>/activate` | 激活算法 |
| POST | `/algorithms/<id>/<version>/<backend>/validate` | 重新校验算法 |
| POST | `/algorithms/<id>/<version>/<backend>/disable` | 禁用算法 |
| POST | `/run` | 执行 ONNX 或 Python Service 算法 |

## 9. 常见问题

### 客户端提示无法连接 8088

确认 `algolib_server` 已启动，并检查：

```text
http://127.0.0.1:8088/health
```

### 返回 `ALGORITHM_NOT_FOUND` 或 `ALGORITHM_NOT_ACTIVE`

算法尚未注册或激活。查询：

```text
GET /algorithms?active_only=false
```

### Python Service 返回不可用

确认对应 Python 服务已经启动，并且 Algorithm Card 中的 `endpoint`、`health_endpoint` 与实际地址一致。本例是 `127.0.0.1:9010`。

### Windows ONNX DLL 加载失败

确认服务器 exe 同目录存在 ONNX Runtime 1.29 的 `onnxruntime.dll`。HTTP 客户端不需要这个 DLL。

### Linux 找不到 `libonnxruntime.so`

启动 Server 前设置：

```bash
export LD_LIBRARY_PATH="/opt/onnxruntime-linux-x64-1.29.0/lib:${LD_LIBRARY_PATH:-}"
```

### 注册算法时报冲突

同一个 `algorithm_id + version + backend_type` 已经注册。注册只需执行一次；直接激活或调用即可。

### 注册路径在客户端存在，但服务器找不到

`package_or_card_path` 是服务器本机路径，不是远程客户端的路径。算法包必须位于运行 `algolib_server` 的机器上。
