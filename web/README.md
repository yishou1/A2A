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

## 当前开发范围

已接入真实健康接口、运行总览、算法目录、算法详情、服务器路径注册、生命周期管理、部署管理、Schema/JSON 在线调用、28 个 KC 功能点矩阵和按 trace ID 执行链路查询。
