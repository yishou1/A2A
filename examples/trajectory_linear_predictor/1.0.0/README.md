# Trajectory Linear Predictor

M03 deterministic two-dimensional ordinary least-squares implementation. The
four coefficients are fitted from each request rather than loaded from a
pretrained artifact.

Regenerate the synthetic reference evaluation and metadata with:

```powershell
python scripts/evaluate_trajectory_linear_predictor.py
```

The reference metrics cover constant-velocity and mildly accelerated synthetic
tracks; they are not operational sensor-performance claims.
