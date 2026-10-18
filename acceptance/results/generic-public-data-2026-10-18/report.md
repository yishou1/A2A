# Generic public-data acceptance

PASS=53 FAIL=0 calls=52

Scope: two generic mathematical modules only. This is not acceptance of the whole library or operational model accuracy.

| Case | Result | Evidence |
| --- | --- | --- |
| data/iris/integrity | PASS | {"rows": 150, "original_source_hashes_verified": true} |
| data/wine/integrity | PASS | {"rows": 178, "original_source_hashes_verified": true} |
| isolated_library_setup | PASS | {"registry_isolated": true, "algorithms": ["clustering_engine", "federated_fedavg_aggregator"]} |
| http/iris/kmeans | PASS | {"dataset_rows": 150, "external_label_ari": 0.645147003482001, "clusters": 3, "inertia_ratio_to_sklearn": 1.0081212632087522} |
| http/iris/dbscan | PASS | {"dataset_rows": 150, "external_label_ari": 0.4706267335681117, "clusters": 2, "reference_ari": 1.0, "noise_fraction": 0.17333333333333334, "label_recovery_acceptance": "NOT_CERTIFIED: reference agreement does not establish class-label recovery"} |
| http/iris/fedavg | PASS | {"clients": 3, "train_rows": 120, "heldout_rows": 30, "maximum_coefficient_error": 3.0742075551870585e-13, "heldout_regression_mae_descriptive_only": 0.2432388338666847} |
| http/wine/kmeans | PASS | {"dataset_rows": 178, "external_label_ari": 0.8635987920128989, "clusters": 3, "inertia_ratio_to_sklearn": 1.0014105908708733} |
| http/wine/dbscan | PASS | {"dataset_rows": 178, "external_label_ari": 0.005010674016359392, "clusters": 1, "reference_ari": 1.0, "noise_fraction": 0.1348314606741573, "label_recovery_acceptance": "NOT_CERTIFIED: reference agreement does not establish class-label recovery"} |
| http/wine/fedavg | PASS | {"clients": 3, "train_rows": 142, "heldout_rows": 36, "maximum_coefficient_error": 4.547473508864641e-13, "heldout_regression_mae_descriptive_only": 214.99212428028983} |
| http/reject/empty_points | PASS | {} |
| http/reject/ragged_points | PASS | {} |
| http/reject/boolean_point | PASS | {} |
| http/reject/excessive_k | PASS | {} |
| http/reject/duplicate_k | PASS | {} |
| http/reject/bad_eps | PASS | {} |
| http/reject/missing_clients | PASS | {} |
| http/reject/missing_round_id | PASS | {} |
| http/reject/duplicate_client | PASS | {} |
| http/reject/zero_count | PASS | {} |
| http/reject/shape_mismatch | PASS | {} |
| http/reject/tensor_key_mismatch | PASS | {} |
| http/reject/ragged_tensor | PASS | {} |
| http/reject/boolean_tensor | PASS | {} |
| http/boundary/nested_tensor_average | PASS | {"sample_weighted_average_matches_oracle": true} |
| http/boundary/single_client_identity | PASS | {} |
| algolib/iris/kmeans | PASS | {"dataset_rows": 150, "external_label_ari": 0.645147003482001, "clusters": 3, "inertia_ratio_to_sklearn": 1.0081212632087522} |
| algolib/iris/dbscan | PASS | {"dataset_rows": 150, "external_label_ari": 0.4706267335681117, "clusters": 2, "reference_ari": 1.0, "noise_fraction": 0.17333333333333334, "label_recovery_acceptance": "NOT_CERTIFIED: reference agreement does not establish class-label recovery"} |
| algolib/iris/fedavg | PASS | {"clients": 3, "train_rows": 120, "heldout_rows": 30, "maximum_coefficient_error": 3.0742075551870585e-13, "heldout_regression_mae_descriptive_only": 0.2432388338666847} |
| algolib/wine/kmeans | PASS | {"dataset_rows": 178, "external_label_ari": 0.8635987920128989, "clusters": 3, "inertia_ratio_to_sklearn": 1.0014105908708733} |
| algolib/wine/dbscan | PASS | {"dataset_rows": 178, "external_label_ari": 0.005010674016359392, "clusters": 1, "reference_ari": 1.0, "noise_fraction": 0.1348314606741573, "label_recovery_acceptance": "NOT_CERTIFIED: reference agreement does not establish class-label recovery"} |
| algolib/wine/fedavg | PASS | {"clients": 3, "train_rows": 142, "heldout_rows": 36, "maximum_coefficient_error": 4.547473508864641e-13, "heldout_regression_mae_descriptive_only": 214.99212428028983} |
| algolib/reject/empty_points | PASS | {} |
| algolib/reject/ragged_points | PASS | {} |
| algolib/reject/boolean_point | PASS | {} |
| algolib/reject/excessive_k | PASS | {} |
| algolib/reject/duplicate_k | PASS | {} |
| algolib/reject/bad_eps | PASS | {} |
| algolib/reject/missing_clients | PASS | {} |
| algolib/reject/missing_round_id | PASS | {} |
| algolib/reject/duplicate_client | PASS | {} |
| algolib/reject/zero_count | PASS | {} |
| algolib/reject/shape_mismatch | PASS | {} |
| algolib/reject/tensor_key_mismatch | PASS | {} |
| algolib/reject/ragged_tensor | PASS | {} |
| algolib/reject/boolean_tensor | PASS | {} |
| algolib/boundary/nested_tensor_average | PASS | {"sample_weighted_average_matches_oracle": true} |
| algolib/boundary/single_client_identity | PASS | {} |
| path_parity/iris/kmeans | PASS | {"output_sha256": "5f3a7b2f5aa86b219f587f3324ac926785b954f6ec57d60512c033b19c4ebeb2"} |
| path_parity/iris/dbscan | PASS | {"output_sha256": "d31eaa5ed261ac3e5be6b0cb20252f885b5b3c9e44821543d2bdfb08d0a1a858"} |
| path_parity/iris/fedavg | PASS | {"output_sha256": "7bc522b10cb98723cd52bb8ea0b04f0e781bacab8c2b551494816d12c516e718"} |
| path_parity/wine/kmeans | PASS | {"output_sha256": "2368562cb688cde96dfacf40d337f4f5f308af43dea0af4da7f55d245579f586"} |
| path_parity/wine/dbscan | PASS | {"output_sha256": "204ea4bfcd5c7f3b0c8f18b9aef05766b0eb96049091466ac15d223276f48f28"} |
| path_parity/wine/fedavg | PASS | {"output_sha256": "3396ae630740acea5acab7c9bd398c95dd3bc54451acc4c555d8894873855e73"} |
