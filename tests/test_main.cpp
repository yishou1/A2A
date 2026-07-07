#include <iostream>

// 中文注释：测试入口保持极简，具体断言放在独立测试文件中。
int RunAlgorithmRegistryTests();
int RunOnnxPhase4Tests();
int RunPhase5ExecutionTests();
int RunHttpServerTests();
int RunPythonServiceValidatorTests();
int RunSchemaValidatorTests();

int main() {
    std::cout.setf(std::ios::unitbuf);
    std::cerr.setf(std::ios::unitbuf);

    const int registry_result = RunAlgorithmRegistryTests();
    const int onnx_phase4_result = RunOnnxPhase4Tests();
    const int phase5_result = RunPhase5ExecutionTests();
    const int http_server_result = RunHttpServerTests();
    const int python_service_result = RunPythonServiceValidatorTests();
    const int schema_result = RunSchemaValidatorTests();
    if (registry_result != 0 || onnx_phase4_result != 0 || phase5_result != 0 ||
        http_server_result != 0 || python_service_result != 0 || schema_result != 0) {
        return 1;
    }
    return 0;
}
