# Threat Priority Random Forest

This M05 package contains a persisted sklearn Random Forest classifier, not a
rule-only substitute. The training script deterministically generates the
repository reference scenarios, performs a stratified holdout evaluation, and
writes SHA256 values for the dataset, script, and model artifact.

The recorded score describes only the repository reference dataset. It must not
be presented as operational battlefield accuracy.

Retrain:

```powershell
python scripts/train_threat_priority_random_forest.py
```

Run strict A2A acceptance:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 -Group m05-random-forest -RealGate
```
