# Public data for general algorithm acceptance

These files support mathematical software tests of clustering and weighted tensor averaging. They are not sensor data or an evaluation of any operational application.

| Dataset | Attribution | Source | License |
| --- | --- | --- | --- |
| Iris | Fisher, R. (1936). Iris [Dataset]. UCI Machine Learning Repository. | https://doi.org/10.24432/C56C76 | CC BY 4.0 |
| Wine | Aeberhard, S. & Forina, M. (1992). Wine [Dataset]. UCI Machine Learning Repository. | https://doi.org/10.24432/C5PC7J | CC BY 4.0 |

License: https://creativecommons.org/licenses/by/4.0/ . Original UCI data and description bytes are retained. The manifest records the exact download sources, retrieval time, and file/archive SHA-256 hashes. Parsers discard blank lines and standardize numeric features only in memory; data files are not corrected, relabeled, or rewritten.

The UCI Iris version differs from the corrected scikit-learn copy at two records. Benchmarks load these exact UCI files, not `sklearn.datasets.load_iris()`.

Downloads are explicit through `scripts/fetch_generic_acceptance_data.py`. Acceptance runs are offline and verify file hashes before making any service calls. An intentional source update requires `--refresh`; do not refresh data merely to make a failed benchmark pass.
