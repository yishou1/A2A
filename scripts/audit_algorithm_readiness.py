"""Read-only algorithm inventory: readiness is not proof of real inference or accuracy.

Run inside a2a-core. Never registers, activates, restarts or predicts with a service.
Only allowlisted execution-mode environment variables are collected from processes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

import yaml


MODE_KEYS = {'TIA_USE_MOCK', 'RTDETR_USE_MOCK', 'RTDETR_COMPUTE_PROFILE',
             'TIA_COMPUTE_PROFILE', 'A2A_CONTAINER_MODE'}


def get_json(url):
    try:
        with urlopen(url, timeout=3) as response:
            return {'http_status': response.status, 'body': json.load(response)}
    except Exception as exc:
        return {'error': str(exc)}


def process_modes(root, algorithm):
    pid_path = root / '.runtime/pids' / f'algorithm-{algorithm}.pid'
    if not pid_path.is_file():
        return {}
    try:
        pid = int(pid_path.read_text().strip())
        command = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
        if f'services/{algorithm}/app/main.py' not in command:
            return {'error': 'PID no longer belongs to the expected algorithm'}
        entries = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        values = {}
        for entry in entries:
            key, _, value = entry.partition(b'=')
            if key.decode(errors='replace') in MODE_KEYS:
                values[key.decode()] = value.decode(errors='replace')
        return values
    except (OSError, ValueError) as exc:
        return {'error': str(exc)}


def inspect_package(root, card_path, active):
    card = yaml.safe_load(card_path.read_text())
    algorithm = card['algorithm_id']
    runtime = card['machine_spec']['runtime']
    result = {'algorithm_id': algorithm, 'version': str(card['version']),
              'backend': card['backend_type'], 'card_status': card.get('status'),
              'registered_active': algorithm in active,
              'correctness': 'NOT_EVALUATED: no labeled representative evaluation was performed'}
    result['declared_usage_limits'] = (card.get('agent_card') or {}).get('when_not_to_use', [])
    result['contract_fixture'] = algorithm == 'onnx_text_classifier'
    if card['backend_type'] == 'onnx':
        path = card_path.parent / runtime.get('model_uri', '')
        result.update(model_file_present=path.is_file(), model_bytes=path.stat().st_size if path.is_file() else 0,
                      assessment='NATIVE_MODEL_PRESENT_NOT_EXECUTED' if path.is_file() else 'MODEL_MISSING')
        return result
    result['health'] = get_json(runtime['health_endpoint'])
    result['metadata'] = get_json(runtime['metadata_endpoint'])
    result['process_modes'] = modes = process_modes(root, algorithm)
    service_path = root / 'commander/services' / algorithm / 'app/main.py'
    source = service_path.read_text() if service_path.is_file() else ''
    result['tia_service'] = tia = 'tia_predictors' in source
    mode = (result['metadata'].get('body') or {}).get('inference_mode')
    if tia and mode is None and 'TIA_USE_MOCK' in modes:
        mode = 'real_requested' if modes['TIA_USE_MOCK'] in ('0', 'false', 'False') else 'mock'
    result['execution_mode'] = mode or 'unspecified'
    health = result['health'].get('body') or {}
    if 'error' in result['health']:
        result['assessment'] = 'HTTP_UNAVAILABLE'
    elif mode == 'mock':
        result['assessment'] = 'MOCK_NOT_REAL'
    elif not health.get('ok') or health.get('model_loaded') is False:
        result['assessment'] = 'NOT_READY'
    elif mode == 'real':
        result['assessment'] = 'REAL_REPORTED_NOT_ACCURACY_VALIDATED'
    else:
        result['assessment'] = 'HTTP_READY_REALITY_UNVERIFIED'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-real', action='store_true', help='Fail unless every package has a checked real/deterministic path and a real native ONNX backend.')
    args = parser.parse_args()
    root = args.root
    registry = get_json('http://127.0.0.1:8088/algorithms?active_only=true')
    if 'error' in registry or not isinstance((registry.get('body') or {}).get('algorithms'), list):
        raise SystemExit(f'Cannot establish active algorithm inventory: {registry}')
    active = {item['algorithm_id'] for item in registry['body']['algorithms']}
    cards = sorted((root / 'commander/examples').glob('*/[0-9]*.[0-9]*.[0-9]*/algorithm_card.yaml'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda card: inspect_package(root, card, active), cards))
    provenance = {}
    integrity = {}
    for base in (root / 'algorithmrepo/models', root / 'commander/models'):
        for path in sorted(base.rglob('*.metadata.json')):
            try:
                key = str(path.relative_to(root))
                metadata = json.loads(path.read_text())
                provenance[key] = metadata
                checks = {}
                for field, hash_field in (('artifact_path', 'artifact_sha256'),
                                          ('generator_source', 'generator_source_sha256')):
                    if metadata.get(field) and metadata.get(hash_field):
                        target = base.parent / metadata[field]
                        actual = None
                        if target.is_file():
                            content = target.read_bytes()
                            if field == 'generator_source':
                                content = content.decode('utf-8').replace('\r\n', '\n').encode('utf-8')
                            actual = hashlib.sha256(content).hexdigest()
                        checks[field] = {'path': str(target.relative_to(root)),
                                         'present': target.is_file(), 'sha256_matches': actual == metadata[hash_field]}
                dataset = metadata.get('dataset') or {}
                if dataset.get('path') and dataset.get('sha256'):
                    target = base.parent / dataset['path']
                    actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
                    checks['dataset'] = {'path': str(target.relative_to(root)),
                                         'present': target.is_file(), 'sha256_matches': actual == dataset['sha256']}
                    if target.is_file():
                        try:
                            canonical = target.read_text(encoding='utf-8').replace('\r\n', '\n').encode('utf-8')
                            checks['dataset']['lf_normalized_sha256_matches'] = hashlib.sha256(canonical).hexdigest() == dataset['sha256']
                        except UnicodeError:
                            pass
                if checks:
                    integrity[key] = checks
            except (OSError, ValueError) as exc:
                provenance[str(path.relative_to(root))] = {'error': str(exc)}
    counts = {}
    for row in rows:
        counts[row['assessment']] = counts.get(row['assessment'], 0) + 1
    cache = root / 'commander/build/CMakeCache.txt'
    onnx_build = next((line for line in cache.read_text().splitlines()
                       if line.startswith('ALGOLIB_WITH_ONNXRUNTIME:')), 'unknown') if cache.is_file() else 'unknown'
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Read-only package, registration, HTTP readiness, process mode and artifact provenance audit; no activation or operational execution.',
              'counts': counts, 'active_registered': len(active), 'packages': rows,
              'artifact_provenance': provenance, 'artifact_integrity': integrity,
              'asset_presence': {relative: (root / relative).is_file() for relative in (
                  'commander/models/checkpoints/graph_relation_gnn_s.safetensors',
                  'algorithmrepo/models/checkpoints/graph_relation_gnn_s.safetensors',
                  'commander/models/conditional_tabular_gan_generator.pt',
                  'algorithmrepo/models/conditional_tabular_gan_generator.pt',
                  'commander/models/checkpoints/motr_tracker.pt',
                  'commander/models/checkpoints/motr_tracker_battlefield.pt',
                  'algorithmrepo/models/checkpoints/motr_tracker.pt',
                  'algorithmrepo/models/checkpoints/motr_tracker_battlefield.pt',
                  'algorithmrepo/models/checkpoints/marl_policy.pt',
                  'algorithmrepo/models/checkpoints/odconv_refiner.pt',
              )},
              'algolib_onnx_build': onnx_build, 'registry_error': registry.get('error')}
    blockers = []
    for row in rows:
        if row.get('contract_fixture'):
            blockers.append(f"{row['algorithm_id']}: constant-logit integration fixture, not a trained semantic classifier")
        if not row['registered_active']:
            blockers.append(f"{row['algorithm_id']}: not active")
        if row['backend'] == 'onnx':
            if onnx_build != 'ALGOLIB_WITH_ONNXRUNTIME:BOOL=ON' or not row.get('model_file_present'):
                blockers.append(f"{row['algorithm_id']}: real native ONNX backend/model unavailable")
        elif (row.get('execution_mode') not in {'real', 'deterministic'}
              or not (row['health'].get('body') or {}).get('ready_for_real')):
            blockers.append(f"{row['algorithm_id']}: {row['assessment']}, real readiness not established")
    report['real_readiness_gate'] = {'passed': not blockers, 'blockers': blockers,
                                      'accuracy_validated': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'packages': len(rows), 'active_registered': len(active), 'counts': counts}, ensure_ascii=False))
    for row in rows:
        print(f"{row['algorithm_id']}: {row['assessment']}; active={row['registered_active']}; mode={row.get('execution_mode', 'native')}")
    if args.require_real and blockers:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
