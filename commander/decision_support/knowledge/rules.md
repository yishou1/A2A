# Rules Knowledge

## RULE-SIM-001: Simulation-only boundary

Tags: simulation, decision-support, handoff

All generated plans in this demo remain simulation-only decision-support
artifacts. A plan may be handed to the next demo module only as structured
analysis, not as an execution command.

## RULE-HUMAN-001: Human review for high-risk recommendations

Tags: human-review, high-risk, authorization

Plans involving high-risk targets, blocked constraints, or uncertain
authorization must require human review before any downstream handoff.

## RULE-BLOCK-001: Prohibited execution wording

Tags: blocking, execution, command

Plans that contain direct execution, strike, fire, launch, or irreversible
action wording are blocked in this demo until rewritten as observation,
monitoring, reassessment, or reporting actions.

## RULE-CONSTRAINT-001: Restricted boundary caution

Tags: restricted, boundary, review

Plans that mention restricted boundaries or restricted zones require review.
If a plan combines restricted-zone language with direct execution wording, it
must be blocked for this demo.

