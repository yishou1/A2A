# Federated FedAvg Aggregator

This M12 package implements actual sample-weighted FedAvg over compatible client
model tensors. It does not return a precomputed global model and does not use a
single-machine classifier as a substitute for federated aggregation.

The reproducible experiment runs three non-IID clients through local logistic
training and eight aggregation rounds. Its dataset, implementation, script, and
final global weights are hash-tracked.

Current scope does not include secure aggregation, differential privacy,
Byzantine-robust aggregation, or production network orchestration.

Run the reference experiment:

```powershell
python scripts/run_federated_fedavg_reference.py
```

Run strict acceptance:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 -Group m12-fedavg -RealGate
```
