#include "algolib/validation/onnx_package_validator.h"

#include "algolib/runtime/onnx_runner.h"
#include "algolib/validation/golden_case_runner.h"

namespace algolib {

Status OnnxPackageValidator::ValidatePackage(const std::filesystem::path& package_root,
                                             const AlgorithmCard& card) const {
    AlgorithmEntry entry;
    entry.key.algorithm_id = card.algorithm_id;
    entry.key.version = card.version;
    entry.key.backend_type = card.backend_type;
    entry.package_root = package_root;
    entry.card_path = package_root / "algorithm_card.yaml";
    entry.card = card;

    OnnxRunner runner;
    auto load_status = runner.Load(entry);
    if (!load_status.ok()) {
        return load_status;
    }

    GoldenCaseRunner golden_case_runner;
    return golden_case_runner.Run(entry, &runner);
}

}  // namespace algolib
