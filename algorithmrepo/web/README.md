# AlgoLib 算法管理台

## 本地开发

1. 首次使用时，在仓库根目录将已验证的本地 ONNX 算法批量纳管到默认 Registry：

   ```powershell
   .\scripts\bootstrap_algorithm_console.ps1
   ```

   脚本默认不自动激活算法；如需同时激活，使用 `-Activate`。算法卡更新后可使用 `-RefreshExisting` 重新验证已纳管的 ONNX 算法。Python HTTP 算法需要相应服务已启动，应在界面中逐个输入服务器包路径注册。

2. 启动 AlgoLib Server，默认监听 `http://127.0.0.1:8088`。
3. 在本目录执行：

   ```powershell
   npm install
   npm run dev
   ```

4. 访问 `http://127.0.0.1:5173`。Vite 会将 `/api/*` 去掉 `/api` 前缀后代理到 AlgoLib Server。

如需修改后端地址，设置 `ALGOLIB_API_TARGET`后再启动 Vite。

页头的“AMOS 任务台”默认在新标签页打开 `http://127.0.0.1:5000/`。如 AMOS
使用其他地址，可在启动 Vite 前设置 `VITE_AMOS_URL`。

## AMOS 同源部署

执行 `npm run build` 时，生产资源默认使用 `/algolib/` 基路径，浏览器 API 请求默认
使用 `/algolib-api`。AMOS 会提供 SPA 路由回退并把 API 转发到 AlgoLib Server，因此
最终用户只需访问 `http://127.0.0.1:5000/algolib/algorithms`。

如需覆盖生产路径，可在构建前设置 `ALGOLIB_WEB_BASE` 和
`VITE_ALGOLIB_API_PREFIX`。Vite 开发模式仍使用根路径和 `/api` 代理，不受生产配置影响。

AMOS、AlgoLib Server 和生产构建都启动后，可执行真实浏览器冒烟验证：

```powershell
npm run test:e2e:integrated
```

在合并后的 Windows 总项目根目录，可以使用
`scripts/start_integrated_ui.ps1` 和 `scripts/stop_integrated_ui.ps1` 统一启停 AMOS、
AlgoLib Server 与生产管理页面，无需单独运行 Vite。

## 当前开发范围

已接入真实健康接口、运行总览、算法目录、算法详情、服务器路径注册、生命周期管理、部署管理、Schema/JSON 在线调用、28 个 KC 功能点矩阵和按 trace ID 执行链路查询。

## 测试与构建

运行单元和组件测试：

```powershell
npm run test
```

首次运行浏览器端到端测试前安装 Chromium：

```powershell
npx playwright install chromium
```

运行 Playwright E2E：

```powershell
npm run test:e2e
```

当前 E2E 使用浏览器网络拦截提供稳定的 API 响应，覆盖总览加载、页面导航、算法目录展示和搜索过滤；真实 AlgoLib Server 的 ONNX/Python HTTP 全生命周期验收将在后续测试中单独覆盖。

仓库存在 `build-smoke-offline/Debug/algolib.exe` 和 `algolib_server.exe` 时，可运行真实 ONNX 浏览器验收：

```powershell
npm run test:e2e:real
```

该命令使用 18088/5174 测试端口，在 `web/test-results/real-e2e/` 创建独立 Registry 和执行日志，不会修改默认 Registry。测试会注册并激活：

- `compliance_risk_scorer_onnx`：验证真实 ONNX Runtime 输出和 KC-21 链路。
- `trajectory_linear_predictor`：在 9011 端口启动真实 Python HTTP 服务，验证速度、瞄准点输出和 KC-19 链路。

默认优先使用 `D:\software\Miniconda3\envs\algorithm_repo\python.exe`；也可以通过 `ALGOLIB_E2E_PYTHON` 指定 Python 解释器。

真实套件同时验证算法禁用后重新验证仍保持 `disabled`、随后可重新激活，以及非法 ONNX 输入会在浏览器端被 Schema 校验拦截而不会发送 `/run` 请求。

在线调用页会将算法卡中的全部对象输入示例和 Schema 缺省输入整理成可选请求模板。加载模板会同时更新表单和 JSON 模式并清空旧结果，但保留当前 request ID 和 trace ID，便于演示人员主动控制链路标识。

Mock E2E 还覆盖 503 服务不可用、504 服务超时和网络中断响应，确认前端保留后端 `error_code`、错误消息、request ID 和 trace ID，不会把已知错误降级显示成 `UNKNOWN_ERROR`。可恢复错误会保留原请求并提供显式重试入口。真实生命周期套件使用隔离注册表和专用算法覆盖页面状态过期时由后端返回的 409 冲突，不会污染默认注册表。

执行生产构建：

```powershell
npm run build
```
