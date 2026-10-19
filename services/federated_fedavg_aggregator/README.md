# Federated FedAvg Aggregator Service

Run from the repository root:

```powershell
python services/federated_fedavg_aggregator/app/main.py
```

The default port is `9034`. The service aggregates same-shaped JSON weight
tensors from multiple clients, weighted by each client's sample count.
