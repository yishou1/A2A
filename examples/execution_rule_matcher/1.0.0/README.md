# Execution Rule Matcher

M02 association-rule implementation. It mines deterministic Apriori-style
rules from repository reference-policy records, verifies the persisted artifact
with SHA256 at service startup, and ranks matching rules by confidence,
specificity, and support.

Rebuild the artifact and evaluation metadata with:

```powershell
python scripts/train_execution_rule_matcher.py
```

The recorded holdout is synthetic policy coverage, not an operational
battlefield benchmark. Decisions still require human review.
