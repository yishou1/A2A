#pragma once

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"
#include "algolib/runtime/health_status.h"

namespace algolib {

class IAlgorithmRunner {
public:
    virtual ~IAlgorithmRunner() = default;

    virtual Status Load(const AlgorithmEntry& entry) = 0;
    virtual AlgorithmResult Run(const AlgorithmRequest& request) = 0;
    virtual HealthStatus HealthCheck() const = 0;
};

}  // namespace algolib
