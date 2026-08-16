# Intent Gaussian Naive Bayes

This M07 package contains a persisted sklearn GaussianNB classifier with
explicit posterior probabilities. Training is deterministic and records hashes
for the model, reference dataset, and training script.

The bundled data represents synthetic class-conditional distributions. Recorded
metrics are reproducibility evidence, not operational performance claims.

Retrain:

```powershell
python scripts/train_intent_gaussian_naive_bayes.py
```

Run strict acceptance:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 -Group m07-naive-bayes -RealGate
```
