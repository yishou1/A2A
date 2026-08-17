# Nacos 路由原理说明

## 1. Nacos 是什么

Nacos 是一个服务发现、服务注册和配置管理平台。它常用于微服务架构中，帮助服务之间动态找到彼此。

在传统写法中，如果 Commander 要调用 Artillery Agent，可能会直接写死：

```text
http://127.0.0.1:8003
```

这种方式的问题是：

- Agent 地址变了，代码也要改。
- 同一类 Agent 有多个实例时，不方便选择。
- 某个 Agent 下线后，调用方不容易自动发现。
- 后续扩展、替换、重规划都比较麻烦。

Nacos 的作用是把这些服务地址集中管理起来。每个 Agent 启动时把自己的地址和能力信息注册到 Nacos，调用方只需要按条件查询 Nacos，就能拿到可用 Agent 的 IP 和端口。

简单理解：

```text
Agent 启动 -> 注册到 Nacos
Commander 需要执行任务 -> 查询 Nacos
Nacos 返回可用 Agent 地址 -> Commander 调用 Agent
```

## 2. Nacos 在本项目中的作用

在 A2A 项目中，Nacos 主要承担“服务注册与发现”的职责。

它不是直接转发请求的网关，也不是反向代理。Commander 最终仍然是直接调用目标 Agent。

也就是说，Nacos 做的是：

```text
告诉 Commander：哪个 Agent 可以处理这个任务，以及它在哪里。
```

不是：

```text
帮 Commander 把请求转发到 Agent。
```

本项目中的调用链路是：

```text
Commander
  -> 查询 Nacos
  -> 得到 Agent IP/端口
  -> 获取 Agent Card
  -> 鉴权
  -> 调用 /sendMessage 或 /sendMessageStream
```

## 3. 本项目中的 Agent 注册方式

每个 Agent 启动时都会调用 `NacosRegistry.register_service()` 注册自己。

以 Recon Agent 为例：

```python
registry.register_service(
    service_name="A2A-Agent",
    ip=ip,
    port=port,
    metadata={"role": "recon", "status": "idle"}
)
```

注册到 Nacos 的核心信息包括：

| 字段 | 含义 | 示例 |
|---|---|---|
| `service_name` | 服务名，同类 Agent 统一注册到这个服务下 | `A2A-Agent` |
| `ip` | Agent 的 IP 地址 | `127.0.0.1` |
| `port` | Agent 的服务端口 | `8002` |
| `metadata.role` | Agent 的角色或能力类型 | `recon` |
| `metadata.status` | Agent 当前状态 | `idle` |

各 Agent 的注册信息大致如下：

| Agent | 端口 | role | metadata |
|---|---:|---|---|
| Recon Agent | `8002` | `recon` | `{"role": "recon", "status": "idle"}` |
| Artillery Agent | `8003` | `artillery` | `{"role": "artillery", "firepower": "heavy", "status": "idle"}` |
| Assault Agent | `8004` | `assault` | `{"role": "assault", "status": "idle"}` |
| Evaluator Agent | `8005` | `evaluator` | `{"role": "evaluator", "status": "idle"}` |

这些 Agent 虽然都注册在 `A2A-Agent` 这个服务名下，但通过 metadata 区分能力。

## 4. 本项目中的 Nacos 路由原理

严格来说，Nacos 这里做的是“服务发现 + metadata 过滤”，项目中把这个过程称为路由。

Commander 不直接写死目标地址，而是先确定需要的角色：

```python
self.delegate_task("artillery", strike_task, stream=True)
```

然后在 `delegate_task()` 中查询 Nacos：

```python
instances = self.registry.discover_service(
    "A2A-Agent",
    {"role": role_needed, "status": "idle"}
)
```

这一步的意思是：

```text
请从 A2A-Agent 这个服务下面，
找出 role 等于 artillery，
并且 status 等于 idle 的健康实例。
```

查询结果可能类似：

```json
{
  "ip": "127.0.0.1",
  "port": 8003,
  "healthy": true,
  "metadata": {
    "role": "artillery",
    "firepower": "heavy",
    "status": "idle"
  }
}
```

然后 Commander 取第一个匹配实例：

```python
target = instances[0]
ip = target.get("ip")
port = target.get("port")
```

最后创建 A2AClient 调用目标 Agent：

```python
client = A2AClient(ip, port)
```

完整流程如下：

```text
任务阶段：火力打击
  -> 代码确定 role_needed = artillery
  -> 查询 Nacos: serviceName=A2A-Agent, role=artillery, status=idle
  -> Nacos 返回 127.0.0.1:8003
  -> Commander 调用 Artillery Agent
```

## 5. NacosRegistry 的实现逻辑

本项目对 Nacos 做了一层封装，文件位于：

```text
registry/nacos_manager.py
```

核心方法有两个：

| 方法 | 作用 |
|---|---|
| `register_service()` | Agent 启动时注册服务实例 |
| `discover_service()` | Commander 根据标签发现服务实例 |

### 5.1 注册服务

注册时会把服务名、IP、端口和 metadata 发送给 Nacos。

当前实现优先使用 Nacos HTTP API：

```python
POST /nacos/v1/ns/instance
```

请求参数包括：

```text
serviceName=A2A-Agent
ip=127.0.0.1
port=8003
metadata={"role":"artillery","firepower":"heavy","status":"idle"}
```

如果 HTTP API 失败，再尝试使用 `nacos-sdk-python`。

### 5.2 发现服务

发现服务时会调用：

```python
GET /nacos/v1/ns/instance/list
```

查询某个服务名下的所有实例：

```text
serviceName=A2A-Agent
```

然后在本地进行两层过滤：

第一层，过滤健康实例：

```python
healthy_instances = [
    i for i in instances.get("hosts", [])
    if i.get("healthy")
]
```

第二层，按 metadata 过滤：

```python
for k, v in required_tags.items():
    if meta.get(k) != v:
        match = False
```

例如：

```python
required_tags = {
    "role": "artillery",
    "status": "idle"
}
```

只有 metadata 同时满足这两个条件的实例才会返回。

## 6. 为什么说这是“路由”

通常意义上的路由，是指根据某些规则选择请求的目标。

在本项目中，路由规则是：

```text
任务需要的 role + Agent 当前状态
```

例如：

| 任务 | 路由条件 | 目标 Agent |
|---|---|---|
| 侦察滩头防御 | `role=recon` | Recon Agent |
| 压制滩头火力 | `role=artillery` | Artillery Agent |
| 评估打击效果 | `role=evaluator` | Evaluator Agent |
| 抢占滩头阵地 | `role=assault` | Assault Agent |

所以这里的 Nacos 路由可以理解为：

```text
根据任务需要的能力标签，从注册中心中找到合适的 Agent 实例。
```

它不是七层网关路由，而是服务发现层面的动态路由。

## 7. 与大模型路由的关系

原版 Nacos 路由中，目标 `role` 是代码提前确定的：

```text
侦察阶段 -> recon
火力打击阶段 -> artillery
评估阶段 -> evaluator
突击阶段 -> assault
```

加入大模型后，大模型并不替代 Nacos，而是替代“确定 role”这一步。

也就是说：

```text
原版：
任务 -> 代码确定 role -> Nacos 查询 Agent -> 调用 Agent

大模型版：
任务 -> 大模型判断 role -> Nacos 查询 Agent -> 调用 Agent
```

Nacos 在两种模式下都仍然负责：

```text
根据 role 和 status 找到可用 Agent 实例。
```

区别只是 role 从哪里来。

## 8. Nacos 是否可以本地化

可以。Nacos 完全可以本地化部署。

本项目已经提供了本地化部署方式，使用 Docker Compose 启动 Nacos：

```yaml
services:
  nacos:
    image: nacos/nacos-server:v2.2.3
    container_name: a2a-nacos-standalone
    environment:
      - PREFER_HOST_MODE=hostname
      - MODE=standalone
      - SPRING_DATASOURCE_PLATFORM=empty
    ports:
      - "8848:8848"
      - "9848:9848"
      - "9849:9849"
```

启动命令：

```bash
cd /home/yl/yl/jzz/A2A
docker compose up -d
```

启动后可以通过本机访问：

```text
http://127.0.0.1:8848/nacos
```

本项目默认连接地址也是本地 Nacos：

```python
NacosRegistry(server_addresses="127.0.0.1:8848", namespace="public")
```

因此，本项目当前就是本地化使用 Nacos 的模式。

## 9. Nacos 本地化部署模式

Nacos 常见有两种部署方式：

| 部署方式 | 说明 | 适用场景 |
|---|---|---|
| 单机模式 | 一个 Nacos 实例，本项目当前使用方式 | 本地开发、实验、Demo |
| 集群模式 | 多个 Nacos 实例组成集群 | 生产环境、高可用场景 |

本项目当前使用的是单机模式：

```text
MODE=standalone
```

优点：

- 部署简单。
- 本地即可运行。
- 不依赖外部云服务。
- 适合开发和性能测试。

限制：

- 单点故障。
- 不适合高可用生产环境。
- 数据持久化能力较弱，当前配置更偏 Demo。

如果后续进入生产环境，可以考虑：

- 使用 Nacos 集群模式。
- 接入 MySQL 作为持久化数据库。
- 配置鉴权和权限控制。
- 配置健康检查与监控。
- 将 Agent 注册信息与部署平台联动。

## 10. 当前实现的注意点

本项目当前 Nacos 封装中有两个工程处理：

### 10.1 使用 HTTP API fallback

测试中发现 `nacos-sdk-python<2.0.0` 访问当前 Nacos 版本时可能出现：

```text
All server are not available
```

因此 `NacosRegistry` 中增加了 HTTP API fallback，优先通过 HTTP API 注册和发现。

### 10.2 禁用环境代理

当前环境中存在 `HTTP_PROXY` / `HTTPS_PROXY`，Python `requests` 默认可能把访问 `127.0.0.1` 的请求也转发到代理，导致本地 Nacos 请求失败。

因此代码中设置：

```python
self.http.trust_env = False
```

用于确保访问本地 Nacos 时不走外部代理。

## 11. 总结

本项目中的 Nacos 主要负责 Agent 的注册与发现。

它的路由原理可以总结为：

```text
Agent 注册自身 IP、端口和 metadata
Commander 根据任务需要的 role 查询 Nacos
Nacos 返回健康实例列表
项目代码按 metadata 过滤出匹配 Agent
Commander 直接调用目标 Agent 的 A2A 接口
```

Nacos 可以完全本地化部署。本项目当前通过 Docker Compose 运行本地单机 Nacos，适合开发、测试和 Demo 场景。

如果追求低延迟，Nacos metadata 路由非常合适；如果需要复杂语义判断，可以在 Nacos 路由之前增加规则、embedding 或大模型来决定 role，但最终服务发现仍然可以交给 Nacos 完成。
