# Decision Plan Recommender ONNX

Native ONNX package for a reproducibly trained candidate-plan logistic-
regression reference model. It scores one precomputed eight-feature row and
does not generate, rank, or validate plans. Those orchestration responsibilities
remain in `decision_planning_core`.

Feature order:

1. `coverage`
2. `risk_alignment`
3. `resource_efficiency`
4. `constraint_fit`
5. `authorization`
6. `lstm_trend`
7. `priority`
8. `objective_fit`

Rebuild the reference dataset, model, metadata, and holdout metrics with
`python scripts/train_logistic_onnx_models.py`. The dataset contains synthetic
reference-policy labels and is not a production decision benchmark.

