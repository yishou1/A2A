# Compliance Risk Scorer ONNX

Native ONNX package for a reproducibly trained compliance-risk logistic-
regression reference model. It scores one precomputed six-feature row. Rule
retrieval, authorization checks, explanations, and fallback behavior remain in
`compliance_authorization_core`.

Feature order:

1. `blocking_violation_count`
2. `warning_violation_count`
3. `authorization_status_score`
4. `authorization_out_of_scope`
5. `rag_evidence_count`
6. `law_of_war_rule_hit`

Rebuild the reference dataset, model, metadata, and holdout metrics with
`python scripts/train_logistic_onnx_models.py`. The dataset contains synthetic
reference-policy labels and is not a production compliance benchmark.
