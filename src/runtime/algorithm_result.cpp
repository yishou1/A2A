#include "algolib/runtime/algorithm_result.h"

namespace algolib {

nlohmann::json ToJson(const AlgorithmResult& result) {
    nlohmann::json json_value{
        {"ok", result.ok},
        {"request_id", result.request_id},
        {"trace_id", result.trace_id},
        {"algorithm_id", result.algorithm_id},
        {"version", result.version},
        {"backend_type", ToString(result.backend_type)},
        {"outputs", result.outputs},
        {"usage", result.usage},
        {"error", nullptr},
    };

    if (result.error.has_value()) {
        json_value["error"] = {
            {"code", result.error->code},
            {"message", result.error->message},
        };
    }

    if (result.function_execution.has_value()) {
        const auto& execution = result.function_execution.value();
        json_value["function_execution"] = {
            {"function_id", execution.function_id},
            {"function_code", execution.function_code},
            {"function_name", execution.function_name},
            {"role", execution.role},
            {"coverage_level", execution.coverage_level},
            {"mapping_source", execution.mapping_source},
            {"execution_status", execution.execution_status},
            {"workflow_instance_id", execution.workflow_instance_id},
            {"step_instance_id", execution.step_instance_id},
            {"matched", execution.matched},
        };
    }

    return json_value;
}

}  // namespace algolib
