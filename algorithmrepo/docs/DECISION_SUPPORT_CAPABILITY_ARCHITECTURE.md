# Decision-support capability architecture

## Responsibility boundaries

```text
Decision-planning / compliance Agent
  - prompt and LLM interaction
  - request validation and capability selection
                    |
                    v
decision_support shared workflows
  - candidate generation and feature preparation
  - structured rules and conservative fallback policy
                    |
                    v
decision_planning_core / compliance_authorization_core
  - algorithm orchestration and stable service contracts
                    |
        +-----------+-----------+-----------+----------------+
        |           |           |           |                |
        v           v           v           v                v
 plan recommender  trend      risk       rule-table      SynapseRAG
      ONNX       predictor   scorer       matching     HTTP retrieval
                   ONNX       ONNX
```

The Agent layer does not load model files. The core services do not contain
private ONNX assets. Every model capability owns a versioned algorithm package,
and SynapseRAG remains a separately deployed HTTP service with independent data
and runtime state.

## Independent capabilities

| Capability | Package or interface | Consumer |
|---|---|---|
| Candidate-plan recommendation | `decision_plan_recommender_onnx:1.0.0` | planning core |
| Target-trend prediction | `target_trend_predictor_onnx:1.0.0` | planning core |
| Compliance-risk calibration | `compliance_risk_scorer_onnx:1.0.0` | compliance core |
| Structured rule matching | `decision_support.knowledge.rule_tables` | both cores and local mode |
| Evidence retrieval | SynapseRAG `POST /api/retrieve` | shared RAG interface |

`decision_capabilities.py` is the adapter between domain feature dictionaries
and generic ONNX tensor contracts. Runtime output includes `algorithm_id`,
`version`, backend, model path, and fallback state so callers can identify the
actual capability used.

## Runtime modes

In `DECISION_AGENT_BACKEND=local`, the Agent calls the shared workflows with
deterministic formula capabilities. This mode does not require AlgoLib or
ONNX Runtime.

In `DECISION_AGENT_BACKEND=algolib`, the Agent invokes one of the two core
services. The core then composes the independent ONNX packages and the shared
RAG interface. Model or runtime failures fall back to formulas; RAG failures
follow the existing conservative planning and compliance policies.

Full-stack startup places both `commander/` and `algorithmrepo/services/` on
`PYTHONPATH`. To run a core service directly, use the same paths:

```bash
export PYTHONPATH="$PWD/commander:$PWD/algorithmrepo/services"
```

`algorithmrepo/services/a2a_algorithms_common` is canonical for full-stack
execution. The copy under `commander/services` is retained only for Commander’s
standalone fixture layout and must remain byte-identical for shared adapters.
