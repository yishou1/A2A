# 算法库 HTTP 调用快速指南（Windows / Linux）

## 1. 最终调用方式

外部 C++ 程序统一调用：

```text
POST http://127.0.0.1:8088/run
```

服务器根据请求中的 `backend_type` 自动选择后端：

```text
C++ 客户端 -> algolib_server:8088
                    ├── onnx -> ONNX Runtime -> model.onnx
                    └── python_http_service -> Python Service
```

C++ 客户端不需要直接链接算法库，也不需要安装 ONNX Runtime。ONNX Runtime 只安装在运行 `algolib_server` 的机器上。

## 2. Windows 环境和编译

### 2.1 环境要求

- Windows x64。
- Visual Studio 2022 C++ Build Tools。
- CMake 3.20 或更高版本。
- Ninja（推荐，可选）。
- ONNX Runtime C++ SDK 1.29 x64。
- Python 3.11；运行 Python Service 时需要。

当前机器已经具备：

```text
ONNX SDK：D:\Desktop\algorithm repo1\build-deps\onnxruntime-win-x64-1.29.0
Python 环境：C:\Users\liu\.conda\envs\algolib
已验证构建：D:\Desktop\algorithm repo1\build-zsl-verify
```

如果不修改 C++ 源码，可以直接使用 `build-zsl-verify`，无需重新编译。

### 2.2 Windows 编译

在 Visual Studio Developer PowerShell 中执行：

```powershell
cd "D:\Desktop\algorithm repo1"

$OrtRoot = "D:\Desktop\algorithm repo1\build-deps\onnxruntime-win-x64-1.29.0"

cmake -S . -B build-windows -G Ninja -DALGOLIB_WITH_ONNXRUNTIME=ON -DALGOLIB_ONNXRUNTIME_ROOT="$OrtRoot"
cmake --build build-windows --parallel
ctest --test-dir build-windows --output-on-failure
```

没有 Ninja 时去掉 `-G Ninja`。Visual Studio 多配置生成器还需要在构建、测试命令中指定 `--config Release` 和 `-C Release`。

Ninja 构建结果：

```text
build-windows\algolib.exe                 注册和管理算法
build-windows\algolib_server.exe          HTTP Server
build-windows\algorithm_http_client.exe   C++ HTTP 客户端样例
build-windows\onnxruntime.dll             ONNX 运行库
```

## 3. Linux 环境和编译

### 3.1 环境要求

- x86_64 Linux。
- 支持 C++17 的 GCC 或 Clang。
- CMake 3.20 或更高版本。
- Make 或 Ninja。
- ONNX Runtime C++ SDK 1.29 Linux x64。
- Python 3.11 和 `venv`；运行 Python Service 时需要。
- curl；用于检查 HTTP 服务。

Ubuntu/Debian 可先安装基础工具，具体包名以发行版为准：

```bash
sudo apt update
sudo apt install build-essential cmake ninja-build python3 python3-venv curl
```

将 Linux 版 ONNX Runtime SDK 解压到例如：

```text
/opt/onnxruntime-linux-x64-1.29.0
```

不能在 Linux 使用 Windows 的 `onnxruntime.dll`；Linux 使用 `libonnxruntime.so`。

### 3.2 Linux 编译

假设仓库位于 `/opt/algorithm-repo`：

```bash
cd /opt/algorithm-repo

export ORT_ROOT=/opt/onnxruntime-linux-x64-1.29.0

cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release -DALGOLIB_WITH_ONNXRUNTIME=ON -DALGOLIB_ONNXRUNTIME_ROOT="$ORT_ROOT"
cmake --build build-linux --parallel
ctest --test-dir build-linux --output-on-failure
```

Linux 构建结果没有 `.exe` 后缀：

```text
build-linux/algolib
build-linux/algolib_server
build-linux/algorithm_http_client
```

启动 Server 前配置 ONNX 动态库路径：

```bash
export LD_LIBRARY_PATH="$ORT_ROOT/lib:${LD_LIBRARY_PATH:-}"
```

### 3.3 Linux Python 环境

```bash
cd /opt/algorithm-repo
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r services/requirements.txt
```

## 4. 启动算法库 HTTP Server

### Windows

```powershell
cd "D:\Desktop\algorithm repo1"
.\build-zsl-verify\algolib_server.exe --host 127.0.0.1 --port 8088 --registry .\.algolib\registry.json
```

### Linux

```bash
cd /opt/algorithm-repo
export LD_LIBRARY_PATH="$ORT_ROOT/lib:${LD_LIBRARY_PATH:-}"
./build-linux/algolib_server --host 127.0.0.1 --port 8088 --registry ./.algolib/registry.json
```

保持 Server 终端运行。健康检查：

```text
http://127.0.0.1:8088/health
```

Windows：

```powershell
Invoke-RestMethod http://127.0.0.1:8088/health
```

Linux：

```bash
curl -sS http://127.0.0.1:8088/health
```

## 5. 首次注册和激活算法

注册只需执行一次。以下命令和 Server 必须使用同一个 `.algolib/registry.json`。

### 5.1 ONNX 示例

Windows：

```powershell
.\build-zsl-verify\algolib.exe register .\examples\compliance_risk_scorer_onnx\1.0.0
.\build-zsl-verify\algolib.exe activate compliance_risk_scorer_onnx 1.0.0 onnx
```

Linux：

```bash
./build-linux/algolib register ./examples/compliance_risk_scorer_onnx/1.0.0
./build-linux/algolib activate compliance_risk_scorer_onnx 1.0.0 onnx
```

### 5.2 Python Service 示例

先启动 `execution_rule_matcher`。

Windows：

```powershell
conda activate algolib
python services\execution_rule_matcher\app\main.py
```

Linux：

```bash
source .venv/bin/activate
python services/execution_rule_matcher/app/main.py
```

保持 Python Service 运行，再打开另一个终端注册并激活。

Windows：

```powershell
.\build-zsl-verify\algolib.exe register .\examples\execution_rule_matcher\1.0.0
.\build-zsl-verify\algolib.exe activate execution_rule_matcher 1.0.0 python_http_service
```

Linux：

```bash
./build-linux/algolib register ./examples/execution_rule_matcher/1.0.0
./build-linux/algolib activate execution_rule_matcher 1.0.0 python_http_service
```

Python Service 默认监听 `127.0.0.1:9010`。按 `Ctrl+C` 停止服务。

## 6. C++ 调用 HTTP Server

Windows 和 Linux 使用同一份源码：

```text
examples/cpp_http_client/main.cpp
```

核心代码：

```cpp
#include <httplib.h>
#include <nlohmann/json.hpp>

// request 是包含 algorithm_id、version、backend_type、inputs 的 JSON。
nlohmann::json request = /* 读取或构造请求 */;

// 连接算法库 Server。
httplib::Client client("127.0.0.1", 8088);

// ONNX 和 Python Service 都调用同一个 /run 接口。
auto response = client.Post(
    "/run",
    request.dump(),
    "application/json"
);

if (!response) {
    // 无法连接 8088。
}

std::cout << response->body << std::endl;
```

### 6.1 C++ 调用 ONNX

请求文件：

```text
examples/cpp_http_client/requests/onnx_request.json
```

Windows：

```powershell
.\build-zsl-verify\algorithm_http_client.exe .\examples\cpp_http_client\requests\onnx_request.json
```

Linux：

```bash
./build-linux/algorithm_http_client ./examples/cpp_http_client/requests/onnx_request.json
```

成功标志：

```json
"ok": true,
"backend_type": "onnx",
"session_backend": "onnxruntime"
```

### 6.2 C++ 调用 Python Service

确认 `execution_rule_matcher` 正在 9010 端口运行。

请求文件：

```text
examples/cpp_http_client/requests/python_service_request.json
```

Windows：

```powershell
.\build-zsl-verify\algorithm_http_client.exe .\examples\cpp_http_client\requests\python_service_request.json
```

Linux：

```bash
./build-linux/algorithm_http_client ./examples/cpp_http_client/requests/python_service_request.json
```

成功标志：

```json
"ok": true,
"backend_type": "python_http_service"
```

## 7. Windows 和 Linux 的主要区别

| 项目 | Windows | Linux |
| --- | --- | --- |
| 程序 | `.exe` | 无 `.exe` 后缀 |
| ONNX 动态库 | `onnxruntime.dll` | `libonnxruntime.so` |
| 动态库配置 | DLL 放在 exe 旁边 | 设置 `LD_LIBRARY_PATH` |
| 路径分隔符 | `\` | `/` |
| Shell | PowerShell | Bash |
| C++ HTTP 请求 | 相同 | 相同 |
| `/run` 请求 JSON | 相同 | 相同 |

Windows 编译出的 `.exe` 不能复制到 Linux 直接运行；必须在 Linux 重新编译。`model.onnx` 和请求 JSON 可以跨平台使用。

Windows 注册表中包含 Windows 路径，不能直接复制到 Linux。部署 Linux 后应重新注册算法。

## 8. 常见问题

### 无法连接 8088

`algolib_server` 没有启动、端口被占用或客户端地址错误。先检查 `/health`。

### `ALGORITHM_NOT_FOUND` / `ALGORITHM_NOT_ACTIVE`

算法没有注册或激活，或者 CLI 与 Server 使用了不同的注册表文件。

### Python Service 不可用

确认服务已经启动，并检查：

```text
http://127.0.0.1:9010/health
```

### Windows ONNX 无法加载

确认 `onnxruntime.dll` 1.29 与 `algolib_server.exe` 位于同一目录。

### Linux 找不到 `libonnxruntime.so`

启动 Server 前设置：

```bash
export LD_LIBRARY_PATH="$ORT_ROOT/lib:${LD_LIBRARY_PATH:-}"
```

### 注册时报算法已存在

算法已经注册，不要重复执行 `register`；直接激活或运行。

### 输入校验失败

请求的 `inputs` 必须符合对应算法包中的 `input.schema.json`。

### PowerShell 无法激活 Conda

正确命令是：

```powershell
conda activate algolib
```

不是 `conda active algolib`。

### 远程客户端调用

Server 使用 `--host 0.0.0.0`，客户端传服务器 IP 和 8088 端口。当前 Server 没有内置鉴权和 TLS，不要直接暴露到公网。
