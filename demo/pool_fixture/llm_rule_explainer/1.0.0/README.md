# Python HTTP Service LLM Explainer 示例

这个目录提供一个最小可运行的 `python_http_service` 算法包示例，用于演示和验证：

- `register / validate / activate / run` 主流程
- `GET /health -> GET /metadata -> POST /predict` 联调链路
- 输入输出 schema 校验

目录里的关键文件：

- `algorithm_card.yaml`
- `input.schema.json`
- `output.schema.json`
- `golden_cases/case_001_request.json`
- `golden_cases/case_001_response.json`
- `service_contract.md`

更完整的后端文件格式和接口格式说明见仓库根目录：

- [backend_file_formats.md](</c:/Users/liu/Desktop/algorithm repo1/backend_file_formats.md>)
