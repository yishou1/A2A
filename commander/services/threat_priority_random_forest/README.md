# Threat Priority Random Forest Service

Run from the repository root:

```powershell
python services/threat_priority_random_forest/app/main.py
```

The default port is `9032`. The service refuses readiness when the persisted
model is missing or its SHA256 does not match the training metadata.
