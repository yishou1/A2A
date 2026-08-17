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

    return json_value;
}

}  // namespace algolib
