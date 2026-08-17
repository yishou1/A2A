# Clustering Engine Service

Run from the repository root:

```powershell
python services/clustering_engine/app/main.py
```

The service listens on port `9031` by default. Set `PORT` to override it.

Use `params.algorithm=kmeans` with `params.k`, or
`params.algorithm=dbscan` with `params.eps` and `params.min_samples`.
