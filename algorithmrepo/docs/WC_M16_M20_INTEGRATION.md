# WC：M16 分类、M20 时序模型与 Track Agent 集成

## 分支与提交边界

- 工作分支：`wc/track-threat-maritime-latency-fix`。
- 修改前 HEAD：`f5dde565ae8713b12ca51c3fcfa5152e636a08f3`，已包含此前的传输/持久化优化。
- 基线：`integration/maritime-algolib` 的 `cb2b6f402b43c4d346cbd16143e8fea1e5a697bd`。
- GRU 模型来源：`wc/m20-graph-relation-onnx`，提交 `dda1d1e116677048c998b643a6e4640d315e8307`。
- 实现阶段未提交；经用户确认后在当前 WC 分支提交并推送改动及新增模型文件，
  向 `integration/maritime-algolib` 创建 PR，留待审核，不直接合并。
- PR 包含此前的 Agent 传输优化及本次集成差异，无需重新上传/覆盖整个项目。

## 本轮修改

1. `trajectory_predictor:1.0.0` 卡片与 HTTP metadata 改为 M16 时间序列预测模型；
   ST-GNN 实现与 M16 LSTM `target_trend_predictor_onnx` 保持不同 algorithm_id。
   不更改 ST-GNN 权重、输入输出字段、版本或 URL。
2. 新增 `graph_relation_reasoner:2.0.0/python_http_service`，导入原分支的类型路由器、
   四个多帧 GRU-GNN ONNX 专家及来源报告。权重通过 manifest SHA256 核对，未重新训练。
   新包使用规范目录 `examples/graph_relation_reasoner/2.0.0`，不再沿用原分支含 `_onnx` 的目录名。
3. 复用已有 `track_threat_algorithms` Python HTTP 服务。该门面负责原始航迹 JSON、
   历史帧到固定 ONNX 张量的转换、类型路由、模型推理及群组解码；无需单独新增进程。
4. 默认 M20 服务只跑时序 GRU，健康检查验证实际时序模型。旧单帧函数与包留作追溯，
   但不作为默认服务；请求旧版本会返回 404，模型不可加载返回 503，不伪装成功回退。
5. 补全 M20 health/metadata 地址、详细输入及 groups/relations/routing 输出 Schema。
   非法业务参数沿用原生服务现有 `HTTP 200 + ok=false`，外层类型错误为 422；
   统一算法库 `/run` 输入 Schema 错误为 422。
6. 启动扫描改为按 algorithm_id/backend 选择最高稳定版本；新 M20 激活后才停用旧注册项。
   Agent 同样按数值版本选包，并排除旧的 M20 1.x，避免目录/返回顺序造成旧包被选中。
7. Agent 使用 GRU 的群体成员结果，保留本地稳定 ID、生命周期和包络补齐。
   仅对明确路由拒绝的节点或可选调用失败采用物理回退，不用规则覆盖有效模型的负结果。
   群体类型、关系置信度与时序模型来源保留到输出；兼容 ground_group 类型。
8. 历史不足、静态/未知类别、每专家超出 10 个节点均有逐节点 routing 原因；
   不静默截断，也不把这些情况说成成功模型分组。
9. 原有精简响应、SQLite 幂等缓存、紧凑持久化继续保留；本轮未覆盖其实现文件。
10. `algorithmrepo` 与实际启动用的 `commander` 服务/模型目录同步更新，避免只改副本。

## 验证（2026-10-18）

使用已有 WSL 环境 `/opt/a2a/envs/a2a/bin/python`：Python 3.11、ONNX Runtime 1.30.0、
NumPy 2.4.6、FastAPI 0.115.14。无需本次安装新运行依赖；测试额外使用 pytest/jsonschema。

- 算法库专项：27 passed；包含四专家真实推理、HTTP 正常/错误/模型缺失、双目录模型一致性。
- commander 服务专项：7 passed。
- Agent 全量：155 passed、1 skipped、9 failed。9 个失败均在修改前 f5dde565 独立副本复现，
  涉及旧运行模式断言、内嵌飞机模型可用性及 Nacos 心跳状态；本轮未扩大修改这些问题。
- Agent 受影响测试集：57 passed、1 skipped，覆盖 M20、版本选择、群组生命周期、
  A2A 响应及持久化缓存。
- 使用独立临时注册表、真实 C++ 算法库和两个目录的真实 Python 服务，完成
  `/predict`、注册/激活/load、`/run`、Schema 校验、非法请求和真实 Agent 群体阶段验证。
- 最后一轮样例 M20 `/run`：21.615、25.029、26.147、24.363、23.345 ms；
  Agent 群体阶段 21.060 ms，无本地回退。这些不是全系统端到端时延，也不是精度验收。
- 测试专用进程已自动关闭；未改现有部署注册表或启动整套作战剧本。

证据在仓库外的 `../m20_validation_20261018/`，包括启动命令/版本、脱敏请求响应、
服务日志、执行日志及 JUnit XML。验证脚本复用了上次验收构建的 C++ 网关；其 ONNX
原生后端关闭不妨碍此次 HTTP 门面内部使用真实 Python ONNX Runtime。

其他既有发现未纳入本轮修改：旧单帧模型训练脚本的文件字节哈希测试在当前 Windows
检出副本失败；M16 既有 Schema 未要求逐航迹 lat，缺少该字段仍可能被容错接受。
这些不能据本轮正常请求通过就称为已解决。

## 部署提示

本轮修改和验证的工作副本为
`C:\Users\PPRush\Desktop\CLUB\A2A-integration`，WSL 对应路径为
`/mnt/c/Users/PPRush/Desktop/CLUB/A2A-integration`。

原启动目录 `/opt/a2a/workspace/A2A-integration` 仍是 `7e0b88c`，卡片仍为 M06；
本轮没有同步/覆盖该旧目录。继续从旧目录启动不会体现本轮修改。
正式使用前需同步确认后的版本并停止、重启相关进程，不能只刷新页面。
