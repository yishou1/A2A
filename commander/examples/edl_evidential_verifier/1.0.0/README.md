# EDL Evidential Verifier

TIA python_http_service package for `edl_evidential_verifier`.

The real small profile loads the trained 290-parameter Dirichlet evidential
classifier from `models/checkpoints/edl_head_s.safetensors`. It never treats a
missing checkpoint as a valid randomly initialized model.

Each candidate is returned in `assessments` and partitioned into
`verified_detections`, `rejected_detections`, or `review_queue`. Real-mode
assessments include input feature values, class probabilities, class evidence,
Dirichlet alpha/strength, epistemic and aleatoric uncertainty, and the two
threshold comparisons that produced the decision.

The deterministic 1000-row synthetic holdout produced accuracy 0.903,
macro-F1 0.901410, Brier score 0.066736, and 10-bin ECE 0.035839. The raw
detector-confidence baseline had Brier score 0.105480 and ECE 0.136591. These
figures validate calibration plumbing only; they are not operational detector
performance claims.

## Human review protocol

An item enters `review_queue` when its epistemic uncertainty exceeds the
configured maximum or its verification probability lies within the configured
margin around the decision threshold. A reviewer must inspect the source image,
bounds, feature trace, and evidence values before accepting or rejecting it;
downstream tracking must not treat `manual_review` as verified.

## Train and run

```powershell
python scripts/train_edl_evidential_verifier.py
$env:TIA_USE_MOCK="0"
$env:TIA_COMPUTE_PROFILE="small"
python services/edl_evidential_verifier/app/main.py
```
