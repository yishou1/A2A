# Decision Plan Recommender ONNX

Native ONNX package for the bootstrap candidate-plan logistic-regression model.
It scores one precomputed eight-feature row and does not generate, rank, or
validate plans. Those orchestration responsibilities remain in
`decision_planning_core`.

Feature order:

1. `coverage`
2. `risk_alignment`
3. `resource_efficiency`
4. `constraint_fit`
5. `authorization`
6. `lstm_trend`
7. `priority`
8. `objective_fit`

The model is copied from `models/decision_planning_lr.onnx`. Its metadata marks
it as a bootstrap model rather than a production-data-trained model.

