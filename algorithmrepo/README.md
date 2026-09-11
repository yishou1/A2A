# AlgoLib 四类接口 Demo 运行报告

**运行时间：** 2026-08-15T10:43:29Z  
**源码根目录：** `Algorithmrepo/`  
**报告文件：** `demo/demo_output.json`  
**总体结果：✅ 全部 25 个步骤通过，0 失败**

---

## 一、整体架构说明

Demo 启动一个内嵌的 HTTP Server（随机端口），将四类核心接口的完整调用链依次跑通，每个步骤的**请求体 + 响应体 + 通过状态 + 文字解析**均写入 JSON 报告。被测算法为 `onnx_text_classifier v1.0.0`（ONNX 文本分类器），以及 `llm_rule_explainer v1.0.0`（Python HTTP Service）。

---

## 二、接口一：算法注册与查询接口（Registry API）✅ 9/9

> 管理算法的完整生命周期：注册 → 激活 → 多维查询 → 部署记录管理 → 下线删除。

| 步骤 | 接口 | 输入关键字段 | 输出关键结果 |
|------|------|-------------|-------------|
| **1.1 注册** | POST `/algorithms/register` | `package_or_card_path` = `examples/onnx_text_classifier/1.0.0` | HTTP 201，`status=validated`，算法已写入注册表但尚未激活 |
| **1.2 激活** | POST `.../activate` | 无 Body | HTTP 200，`status=validated → active`，算法可对外提供推理服务 |
| **1.3 按任务族查询** | GET `/algorithms?task_family=text_classification` | 过滤条件：`task_family` | HTTP 200，`count=1`，返回 `onnx_text_classifier` 摘要 |
| **1.4 多维 AND 过滤** | GET `/algorithms?backend=onnx&capability=confidence_score` | 同时指定 `backend` + `capability` | HTTP 200，`count=1`，AND 逻辑命中 |
| **1.5 资源约束过滤** | GET `/algorithms?max_memory=2048` | `max_memory=2048 MB` | HTTP 200，`count=1`（算法 `min_memory_mb=512` ≤ 2048） |
| **1.6 详情 + agent_view** | GET `/algorithms/onnx_text_classifier/1.0.0/onnx` | 无 | HTTP 200，返回 `entry`（完整 card）+ `agent_view`（精简视图，含 `latency_p50=20ms`、`accuracy=0.92`） |
| **1.7 添加部署记录** | POST `.../deployments` | `deploy_id=demo-node-1`，`endpoint=http://demo-node-1:8088`，`deploy_status=unloaded` | HTTP 201，`deployed_at` 自动填充为 UTC 时间戳 |
| **1.8 更新部署状态** | PATCH `.../deployments/demo-node-1/status` | `deploy_status=ready`，`status_message=demo loaded` | HTTP 200，`updated_at` 自动填充；`demo-node-1` 出现在 `agent_view.ready_endpoints` |
| **1.9 禁用并删除** | POST `/disable` + DELETE | 无 | `/disable` 返回 `status=disabled`；DELETE 返回 `status=deleted`；验证：`GET /algorithms?active_only=false` 返回 `count=0` |

**设计亮点：**
- 状态机：`draft → validated → active → disabled → deleted`，每步均有门控
- `agent_view` 专门为 AI agent 提供精简视图，包含 when_to_use / 性能指标 / Schema 摘要
- `deployed_at` / `updated_at` 由服务端自动填充，调用方无需传时间戳

---

## 三、接口二：模型加载接口（Model Load API）✅ 7/7

> 管理 ONNX Session 在内存中的生命周期：加载 → 推理 → 缓存复用 → 卸载 → 热重载。

| 步骤 | 接口 | 输入关键字段 | 输出关键结果 |
|------|------|-------------|-------------|
| **2.1 首次加载** | POST `/load` | `algorithm_id=onnx_text_classifier`，`deploy_id=local/d1` | HTTP 200，`load_status=loaded`，`health.status=ready`；自动回写注册表 `deploy_status=ready` |
| **2.2 验证自动回写** | GET `/algorithms/.../onnx` | 无 | 查看 `entry.deployments[local/d1].deploy_status=ready`，`agent_view.ready_endpoints` 包含该节点 |
| **2.3 执行推理** | POST `/run` | `request_id=demo_req_001`，`inputs.text="Classify this task text."` | HTTP 200，`outputs.label=task`，`confidence=0.96`，`usage.execution_provider=cpu` |
| **2.4 缓存命中** | POST `/load`（再次） | 同 2.1 | HTTP 200，`load_status=already_loaded`（不重建 Session，幂等设计） |
| **2.5 健康检查** | GET `/health` | 无 | `runner_cache_size=1`（当前 1 个 Runner 在内存中） |
| **2.6 卸载** | POST `/unload` | `algorithm_id`、`version`、`deploy_id` | HTTP 200，`load_status=unloaded`；自动回写 `deploy_status=unloaded` |
| **2.7 重新加载** | POST `/load`（卸载后） | 同 2.1 | HTTP 200，`load_status=loaded`（Session 重建成功，可用于模型热更新） |

**设计亮点：**
- `/load` 幂等：重复调用不重建 Session，返回 `already_loaded`
- `/load` 成功后**自动回写** `deploy_status=ready` 到注册表，`/unload` 后自动回写 `unloaded`
- `runner_cache_size` 通过 `/health` 实时暴露，可用于运维监控

---

## 四、接口三：文件服务接口（File Serving API）✅ 4/4

> 为 `http_pull` 分布式部署模式提供基础——远程节点可按需从主节点下载算法包内的任意文件，并内置路径安全防护。

| 步骤 | 请求路径 | 预期行为 | 实际输出 |
|------|---------|---------|---------|
| **3.1 下载 ONNX 模型** | GET `.../files/model.onnx` | HTTP 200，文件完整返回 | `file_size_bytes=244`，`expected_size_bytes=244` ✅，`Content-Type: application/octet-stream` |
| **3.2 下载配置文件** | GET `.../files/preprocess.yaml` | HTTP 200，文本文件正常返回 | `file_size_bytes=126` ✅ |
| **3.3 路径穿越攻击** | GET `.../files/../../../etc/passwd` | **期望 HTTP 400**（安全拦截） | 实际 `http_status=400` ✅，证明 canonical 路径校验有效 |
| **3.4 文件不存在** | GET `.../files/nonexistent.bin` | **期望 HTTP 404** | 实际 `http_status=404` ✅，错误语义正确 |

**设计亮点：**
- 服务器对 `rel_path` 做 canonical 路径计算，凡越出算法包根目录的请求一律拒绝（HTTP 400）
- 支持任意文件类型（`.onnx` / `.yaml` / `.json` 等）流式传输
- 安全测试（步骤 3.3）**预期失败为通过**，是防御性测试的典型用法

---

## 五、接口四：Python HTTP Service 并发池（Python Pool API）✅ 5/5

> 限制对 Python HTTP 服务的并发访问数量，防止高并发场景下将 Python 进程压垮。底层使用 `std::condition_variable` + `mutex` 实现线程安全的连接池。

| 步骤 | 接口 | 输入关键字段 | 输出关键结果 |
|------|------|-------------|-------------|
| **4.1 加载并发池** | POST `/load` | `pool_size=3`，`pool_checkout_timeout_ms=5000` | HTTP 200，`load_status=loaded`；并发池已创建，最多允许 3 个并发连接 |
| **4.2 健康检查** | GET `/health` | 无 | `runner_cache_size=1`（Pool Runner 已进入缓存） |
| **4.3 串行 6 次推理** | POST `/run` × 6 | 每次携带独立 `request_id` + `trace_id` | **6/6 成功**；第 4\~6 次请求自动排队等待前 3 次释放连接，`details[]` 记录每次输出（`label=task`，`confidence=0.85`） |
| **4.4 并发 6 线程** | POST `/run` × 6（`std::thread`） | 6 个线程同时发起 | **6/6 成功**；6 线程 > pool_size=3，证明排队无死锁，并发路径线程安全 |
| **4.5 卸载并发池** | POST `/unload` | `algorithm_id=llm_rule_explainer` | HTTP 200，`load_status=unloaded`；连接池完全销毁 |

**设计亮点：**
- `pool_size` 参数在 `/load` 时传入，灵活配置
- `pool_checkout_timeout_ms` 防止无限等待
- 串行 + 并发双路径覆盖，验证排队逻辑和线程安全性

---

## 六、总结统计

```
╔══════════════════════════════════════════╗
║  接口                  通过步数  总步数  ║
╠══════════════════════════════════════════╣
║  接口一 Registry API      9       9     ║
║  接口二 Model Load API    7       7     ║
║  接口三 File Serving API  4       4     ║
║  接口四 Python Pool API   5       5     ║
╠══════════════════════════════════════════╣
║  合计                    25      25     ║
║  总体结论：✅ 全部通过，0 失败           ║
╚══════════════════════════════════════════╝
```

**每个步骤在 `demo/demo_output.json` 中均包含：**
- `step` — 步骤编号（如 `1.1_register`）
- `method` / `path` — HTTP 方法和路径
- `request` — 完整请求体
- `response` — 完整响应体（含嵌套的 `agent_view`、`deployments` 等）
- `http_status` — 实际 HTTP 状态码
- `passed` — 是否通过断言
- `explanation` — 步骤的中文技术解析
- `timestamp` — 执行时间戳（UTC）

---

## 七、运行方式

```bash
# 编译
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target algolib_demo

# 运行 Demo（结果写入 demo/demo_output.json）
./build/algolib_demo

# 或使用脚本（含编译 + 运行）
bash demo/run_demo.sh
```
