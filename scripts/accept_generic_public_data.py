"""Offline, non-operational public-data acceptance against running generic services.

Only clustering_engine and federated_fedavg_aggregator receive numeric requests.
The CLI registry and execution log are isolated; no operational workflows run.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
import sklearn
from sklearn.cluster import DBSCAN, KMeans
from sklearn.metrics import adjusted_rand_score
from threadpoolctl import threadpool_limits
import yaml


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Acceptance:
    IDS = ('clustering_engine', 'federated_fedavg_aggregator')

    def __init__(self, root, output):
        self.root, self.output = root, output
        self.output.mkdir(parents=True, exist_ok=True)
        self.criteria_path = root / 'acceptance/generic_criteria.json'
        self.criteria = json.loads(self.criteria_path.read_text())
        self.data_root = root / 'acceptance/public_data'
        self.manifest = json.loads((self.data_root / 'manifest.json').read_text())
        self.results, self.calls, self.observations = [], [], {}
        self.attempts = 0
        self.cards = {name: yaml.safe_load((root / 'commander/examples' / name / '1.0.0/algorithm_card.yaml').read_text()) for name in self.IDS}
        self.environment = dict(os.environ)
        self.environment.update(ALGOLIB_REGISTRY_PATH=str(output / 'registry.json'),
                                ALGOLIB_EXECUTION_LOG_PATH=str(output / 'executions.jsonl'))
        self.cli = root / 'commander/build/algolib'

    def case(self, name, action):
        start = time.perf_counter()
        try:
            metrics = action() or {}
            result = {'name': name, 'status': 'PASS', 'metrics': metrics}
        except Exception as exc:
            result = {'name': name, 'status': 'FAIL', 'error': f'{type(exc).__name__}: {exc}'}
        result['elapsed_ms'] = round((time.perf_counter() - start) * 1000, 3)
        self.results.append(result)
        print(f"[{result['status']}] {name}" + (f": {result['error']}" if 'error' in result else ''), flush=True)
        return result['status'] == 'PASS'

    def load_data(self, name):
        source = self.manifest['datasets'][name]
        for member, expected in source['files'].items():
            data = (self.data_root / name / member).read_bytes()
            require(len(data) == expected['bytes'] and hashlib.sha256(data).hexdigest() == expected['sha256'], f'Public source bytes changed: {name}/{member}')
        rows = [line.split(',') for line in (self.data_root / name / f'{name}.data').read_text().splitlines() if line.strip()]
        if name == 'iris':
            x = np.asarray([row[:-1] for row in rows], dtype=float)
            _, y = np.unique([row[-1] for row in rows], return_inverse=True)
        else:
            x = np.asarray([row[1:] for row in rows], dtype=float)
            y = np.asarray([int(row[0]) - 1 for row in rows])
        expected = self.criteria['datasets'][name]
        require(x.shape == (expected['rows'], expected['features']), f'Unexpected data shape: {x.shape}')
        require(np.isfinite(x).all() and len(np.unique(y)) == 3, 'Invalid public dataset values/classes')
        return x, y

    def prepare_library(self):
        require(not Path(self.environment['ALGOLIB_REGISTRY_PATH']).exists(), 'Use a fresh report directory; never overwrite an existing audit registry')
        for algorithm in self.IDS:
            package = self.root / 'commander/examples' / algorithm / '1.0.0'
            for args in (['register', str(package)], ['activate', algorithm, '1.0.0', 'python_http_service']):
                result = subprocess.run([str(self.cli), *args], env=self.environment, cwd=self.root / 'commander', capture_output=True, text=True, timeout=20)
                require(result.returncode == 0, result.stdout + result.stderr)
        return {'registry_isolated': True, 'algorithms': list(self.IDS)}

    def call(self, algorithm, inputs, params, route, expect_ok=True):
        require(algorithm in self.IDS, 'Non-generic algorithm request prohibited in this benchmark')
        attempt = self.attempts
        self.attempts += 1
        payload = {'request_id': f'public-accept-{attempt:04}', 'trace_id': 'non-operational-public-data-acceptance',
                   'algorithm_id': algorithm, 'version': '1.0.0', 'backend_type': 'python_http_service',
                   'inputs': inputs, 'params': {**params, 'require_real': True}}
        start = time.perf_counter()
        request_path = self.output / 'requests' / f'{attempt:04}.json'
        request_path.parent.mkdir(exist_ok=True)
        request_path.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
        cli_exit = None
        if route == 'http':
            endpoint = self.cards[algorithm]['machine_spec']['runtime']['endpoint']
            request = Request(endpoint, data=json.dumps(payload, allow_nan=False).encode(), headers={'Content-Type': 'application/json'})
            try:
                with urlopen(request, timeout=self.criteria['transport']['request_timeout_seconds']) as response:
                    result = json.load(response)
            except HTTPError as exc:
                if expect_ok:
                    raise
                result = json.load(exc)
        else:
            process = subprocess.run([str(self.cli), 'run', str(request_path)], env=self.environment,
                                     cwd=self.root / 'commander', capture_output=True, text=True, timeout=20)
            result = json.loads(process.stdout)
            cli_exit = process.returncode
        elapsed = time.perf_counter() - start
        self.calls.append({'route': route, 'algorithm': algorithm, 'request_id': payload['request_id'],
                           'request_sha256': digest(payload), 'response_sha256': digest(result),
                           'ok': result.get('ok'), 'elapsed_ms': round(elapsed * 1000, 3),
                           'inference_mode': result.get('usage', {}).get('inference_mode'), 'error': result.get('error')})
        if cli_exit is not None:
            require((cli_exit == 0) == expect_ok, f'Unexpected CLI exit {cli_exit}: {result}')
        require(result.get('ok') is expect_ok, f'Unexpected service outcome: {result}')
        require(elapsed <= self.criteria['transport']['maximum_case_seconds'], f'Call exceeded provisional time budget: {elapsed:.3f}s')
        if expect_ok:
            require(result.get('usage', {}).get('inference_mode') == 'deterministic', 'Execution mode is not explicitly deterministic')
            return result['outputs']
        require(bool(result.get('error')), 'Rejected input must have an explicit error')
        return {}

    def clustering(self, name, x, y, algorithm, route):
        points = (x - x.mean(axis=0)) / x.std(axis=0)
        params = copy.deepcopy(self.criteria[algorithm])
        if algorithm == 'kmeans':
            request_params = {key: params[key] for key in ('k', 'max_iterations', 'tolerance')}
            reference = KMeans(n_clusters=3, n_init=10, random_state=self.criteria['seed'], max_iter=300, tol=1e-8).fit(points)
        else:
            request_params = {'eps': self.criteria['datasets'][name]['dbscan_eps'], 'min_samples': params['min_samples']}
            reference = DBSCAN(**request_params).fit(points)
        request_params['algorithm'] = algorithm
        output = self.call('clustering_engine', {'points': points.tolist()}, request_params, route)
        labels = np.asarray(output['labels'])
        require(labels.shape == y.shape and np.issubdtype(labels.dtype, np.integer), 'Bad cluster labels')
        require(sum(item['size'] for item in output['clusters']) + len(output['noise_indices']) == len(x), 'Cluster/noise coverage does not match input')
        for cluster in output['clusters']:
            members = np.flatnonzero(labels == cluster['cluster_id'])
            require(cluster['member_indices'] == members.tolist() and cluster['size'] == len(members), 'Bad membership/size')
            np.testing.assert_allclose(cluster['centroid'], points[members].mean(axis=0), atol=1e-10, rtol=1e-10)
        external_ari = float(adjusted_rand_score(y, labels))
        metrics = {'dataset_rows': len(x), 'external_label_ari': external_ari, 'clusters': output['cluster_count']}
        if algorithm == 'kmeans':
            ratio = float(output['inertia'] / reference.inertia_)
            require(ratio <= params['max_inertia_ratio_to_reference'], f'Inertia ratio {ratio} exceeded predeclared budget')
            require(external_ari >= params['minimum_external_ari'], f'External ARI {external_ari} below declared minimum')
            require(output['converged'] is True, 'K-Means did not converge')
            metrics['inertia_ratio_to_sklearn'] = ratio
        else:
            agreement = float(adjusted_rand_score(reference.labels_, labels))
            np.testing.assert_array_equal(labels == -1, reference.labels_ == -1)
            require(agreement >= params['minimum_reference_ari'], f'DBSCAN reference agreement {agreement}')
            noise_fraction = float((labels == -1).mean())
            require(output['cluster_count'] >= 1 and noise_fraction <= params['maximum_noise_fraction'], 'Degenerate public-data clustering')
            metrics.update(reference_ari=agreement, noise_fraction=noise_fraction,
                           label_recovery_acceptance='NOT_CERTIFIED: reference agreement does not establish class-label recovery')
        repeated = self.call('clustering_engine', {'points': points.tolist()}, request_params, route)
        require(output == repeated, 'Repeated identical input changed deterministic output')
        self.observations[(name, algorithm, route)] = digest(output)
        return metrics

    def fedavg(self, name, x, route):
        permutation = np.random.default_rng(self.criteria['seed']).permutation(len(x))
        train, test = permutation[:int(len(x) * 0.8)], permutation[int(len(x) * 0.8):]
        features, target = x[:, :-1], x[:, -1]
        mean, scale = features[train].mean(axis=0), features[train].std(axis=0)
        design = np.column_stack(((features - mean) / scale, np.ones(len(x))))
        # Local ridge models of plant/chemical measurements, with a held-out subset.
        sorted_train = train[np.argsort(target[train], kind='stable')]
        partitions = np.split(sorted_train, [len(train)//6, len(train)//2])
        updates, coefficients, counts = [], [], []
        for index, indices in enumerate(partitions):
            local = design[indices]
            coef = np.linalg.solve(local.T @ local + 0.001 * np.eye(local.shape[1]), local.T @ target[indices])
            coefficients.append(coef)
            counts.append(len(indices))
            updates.append({'client_id': f'plant-chemistry-client-{index}', 'sample_count': len(indices),
                            'weights': {'coef': coef[:-1].tolist(), 'bias': float(coef[-1])}})
        output = self.call('federated_fedavg_aggregator', {'round_id': f'public-{name}', 'client_updates': updates}, {}, route)
        expected = np.average(np.stack(coefficients), axis=0, weights=counts)
        actual = np.r_[output['aggregated_weights']['coef'], output['aggregated_weights']['bias']]
        tolerance = self.criteria['fedavg']
        np.testing.assert_allclose(actual, expected, atol=tolerance['maximum_absolute_error'], rtol=tolerance['maximum_relative_error'])
        require(output['total_samples'] == len(train) and output['participant_count'] == 3, 'Bad FedAvg participation accounting')
        require(abs(sum(item['aggregation_weight'] for item in output['participants']) - 1) < 1e-10, 'Client weights do not sum to one')
        self.observations[(name, 'fedavg', route)] = digest(output)
        return {'clients': 3, 'train_rows': len(train), 'heldout_rows': len(test),
                'maximum_coefficient_error': float(np.max(np.abs(actual - expected))),
                'heldout_regression_mae_descriptive_only': float(np.mean(np.abs(design[test] @ actual - target[test])))}

    def boundary_cases(self, route):
        valid = {'round_id': 'public-boundary', 'client_updates': [{'client_id': 'a', 'sample_count': 1, 'weights': {'tensor': [[1., 2.], [3., 4.]]}},
                                    {'client_id': 'b', 'sample_count': 3, 'weights': {'tensor': [[5., 6.], [7., 8.]]}}]}
        bad_inputs = [
            ('empty_points', 'clustering_engine', {'points': []}, {}),
            ('ragged_points', 'clustering_engine', {'points': [[1, 2], [3]]}, {}),
            ('boolean_point', 'clustering_engine', {'points': [[True], [2]]}, {}),
            ('excessive_k', 'clustering_engine', {'points': [[1], [2]]}, {'k': 3}),
            ('duplicate_k', 'clustering_engine', {'points': [[1], [1]]}, {'k': 2}),
            ('bad_eps', 'clustering_engine', {'points': [[1], [2]]}, {'algorithm': 'dbscan', 'eps': 0}),
            ('missing_clients', 'federated_fedavg_aggregator', {'round_id': 'invalid', 'client_updates': []}, {}),
            ('missing_round_id', 'federated_fedavg_aggregator', {'client_updates': valid['client_updates']}, {}),
        ]
        for kind in ('duplicate_client', 'zero_count', 'shape_mismatch', 'tensor_key_mismatch', 'ragged_tensor', 'boolean_tensor'):
            bad = copy.deepcopy(valid)
            second = bad['client_updates'][1]
            if kind == 'duplicate_client': second['client_id'] = 'a'
            elif kind == 'zero_count': second['sample_count'] = 0
            elif kind == 'shape_mismatch': second['weights']['tensor'] = [1, 2]
            elif kind == 'tensor_key_mismatch': second['weights'] = {'different': [1]}
            elif kind == 'ragged_tensor': second['weights']['tensor'] = [[1], [2, 3]]
            elif kind == 'boolean_tensor': second['weights']['tensor'][0][0] = True
            bad_inputs.append((kind, 'federated_fedavg_aggregator', bad, {}))
        for name, algorithm, inputs, params in bad_inputs:
            self.case(f'{route}/reject/{name}', lambda a=algorithm, i=inputs, p=params: self.call(a, i, p, route, expect_ok=False))

        def tensor_average():
            output = self.call('federated_fedavg_aggregator', valid, {}, route)
            np.testing.assert_allclose(output['aggregated_weights']['tensor'], [[4, 5], [6, 7]], atol=1e-10)
            return {'sample_weighted_average_matches_oracle': True}
        self.case(f'{route}/boundary/nested_tensor_average', tensor_average)

        def one_client():
            inputs = {'round_id': 'public-single-client', 'client_updates': [valid['client_updates'][0]]}
            output = self.call('federated_fedavg_aggregator', inputs, {'minimum_clients': 1}, route)
            require(output['aggregated_weights'] == inputs['client_updates'][0]['weights'], 'Single-client average must be identity')
        self.case(f'{route}/boundary/single_client_identity', one_client)

    def write_report(self):
        summary = {'pass': sum(case['status'] == 'PASS' for case in self.results),
                   'fail': sum(case['status'] == 'FAIL' for case in self.results), 'calls': len(self.calls), 'request_attempts': self.attempts}
        report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'summary': summary,
                  'accepted_scope': 'Provisional, public non-operational data validation of two generic mathematical modules only.',
                  'entire_algorithm_library_accepted': False, 'operational_accuracy_validated': False,
                  'criteria_sha256': hashlib.sha256(self.criteria_path.read_bytes()).hexdigest(),
                  'criteria': self.criteria, 'data_manifest': self.manifest,
                  'environment': {'python': platform.python_version(), 'numpy': np.__version__, 'sklearn': sklearn.__version__},
                  'implementation_source_sha256': {relative: hashlib.sha256((self.root / relative).read_bytes()).hexdigest() for relative in (
                      'commander/services/a2a_algorithms_common/clustering.py',
                      'commander/services/a2a_algorithms_common/federated_fedavg.py',
                      'commander/services/a2a_algorithms_common/http_service.py')},
                  'cases': self.results, 'calls': self.calls}
        (self.output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        lines = ['# Generic public-data acceptance', '', f"PASS={summary['pass']} FAIL={summary['fail']} calls={summary['calls']}", '',
                 'Scope: two generic mathematical modules only. This is not acceptance of the whole library or operational model accuracy.', '',
                 '| Case | Result | Evidence |', '| --- | --- | --- |']
        for case in self.results:
            evidence = case.get('error') or json.dumps(case['metrics'], ensure_ascii=False)
            lines.append(f"| {case['name']} | {case['status']} | {evidence.replace('|', '/')} |")
        (self.output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(json.dumps(summary))
        return 1 if summary['fail'] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    acceptance = Acceptance(args.root.resolve(), args.output.resolve())
    datasets = {}
    for name in ('iris', 'wine'):
        def load(name=name):
            datasets[name] = acceptance.load_data(name)
            return {'rows': len(datasets[name][0]), 'original_source_hashes_verified': True}
        if not acceptance.case(f'data/{name}/integrity', load):
            return acceptance.write_report()
    if not acceptance.case('isolated_library_setup', acceptance.prepare_library):
        return acceptance.write_report()
    with threadpool_limits(limits=1):
        for route in ('http', 'algolib'):
            for name, (x, y) in datasets.items():
                for algorithm in ('kmeans', 'dbscan'):
                    acceptance.case(f'{route}/{name}/{algorithm}', lambda n=name, x=x, y=y, a=algorithm, r=route: acceptance.clustering(n, x, y, a, r))
                acceptance.case(f'{route}/{name}/fedavg', lambda n=name, x=x, r=route: acceptance.fedavg(n, x, r))
            acceptance.boundary_cases(route)
        for name in datasets:
            for algorithm in ('kmeans', 'dbscan', 'fedavg'):
                def parity(n=name, a=algorithm):
                    left, right = acceptance.observations.get((n, a, 'http')), acceptance.observations.get((n, a, 'algolib'))
                    require(left is not None and left == right, 'Direct HTTP and AlgoLib outputs did not match')
                    return {'output_sha256': left}
                acceptance.case(f'path_parity/{name}/{algorithm}', parity)
    return acceptance.write_report()


if __name__ == '__main__':
    raise SystemExit(main())
