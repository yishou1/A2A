# Compliance Risk Scorer ONNX

Native ONNX package for the bootstrap compliance-risk logistic-regression
model. It scores one precomputed six-feature row. Rule retrieval, authorization
checks, explanations, and fallback behavior remain in
`compliance_authorization_core`.

Feature order:

1. `blocking_violation_count`
2. `warning_violation_count`
3. `authorization_status_score`
4. `authorization_out_of_scope`
5. `rag_evidence_count`
6. `law_of_war_rule_hit`

The model is copied from `models/compliance_authorization_lr.onnx`. Its metadata
marks it as a bootstrap model rather than a production-data-trained model.
