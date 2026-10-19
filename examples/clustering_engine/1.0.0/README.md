# Deterministic Clustering Engine

This M01 package provides two genuine unsupervised CPU algorithms:

- K-Means with deterministic farthest-first centroid initialization.
- DBSCAN with density expansion and explicit `-1` noise labels.

No model checkpoint, random initialization, or substitute execution path is
used. Algorithm parameters are supplied in the top-level request `params`.

Run the service:

```powershell
python services/clustering_engine/app/main.py
```

Validate the complete A2A path:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 -Group m01-clustering -RealGate
```
