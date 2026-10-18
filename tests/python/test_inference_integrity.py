"""Generic transport and checkpoint integrity tests, with no business actions."""
from pathlib import Path
import sys
import json

import pytest
import torch
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'commander/services'), str(ROOT / 'commander')]
from a2a_algorithms_common.http_service import create_algorithm_app
from agent.inference.registry import _load_state_dict


def client(mode=None, predictor=None, loaded=lambda: True):
    return TestClient(create_algorithm_app(
        'example', '1.0.0', 'test', predictor or (lambda inputs, params: {'value': inputs['value']}),
        model_loaded_callable=loaded,
        inference_mode_callable=(lambda: mode) if mode else None,
    ))


def test_mock_is_available_for_demo_but_never_model_loaded():
    api = client('mock')
    health = api.get('/health').json()
    assert health['ok'] is True
    assert health['status'] == 'mock'
    assert health['model_loaded'] is False
    assert health['ready_for_real'] is False
    assert api.get('/metadata').json()['inference_mode'] == 'mock'
    result = api.post('/predict', json={'inputs': {'value': 7}}).json()
    assert result['ok'] is True
    assert result['usage']['inference_mode'] == 'mock'


@pytest.mark.parametrize('mode', ['mock', 'stub', 'unspecified'])
def test_require_real_rejects_before_predictor_is_called(mode):
    called = []
    api = client(mode, predictor=lambda *_: called.append(True))
    result = api.post('/predict', json={'params': {'require_real': True}})
    assert result.status_code == 503
    assert called == []


def test_no_model_check_is_reported_as_unknown_and_rejected_in_strict_mode():
    api = client('real', loaded=None)
    health = api.get('/health').json()
    assert health['model_loaded'] is None
    assert health['model_check_performed'] is False
    assert health['ready_for_real'] is False
    assert api.post('/predict', json={'params': {'require_real': True}}).status_code == 503


@pytest.mark.parametrize('mode', ['real', 'deterministic'])
def test_checked_real_or_deterministic_path_accepts_numeric_input(mode):
    api = client(mode)
    result = api.post('/predict', json={'inputs': {'value': 17}, 'params': {'require_real': True}}).json()
    assert result['ok'] is True
    assert result['outputs']['value'] == 17
    assert result['usage']['inference_mode'] == mode


@pytest.mark.parametrize('output', [{'runtime': {'backend': 'physics_fallback'}}, {'model': {'fallback': True}}, {'runtime': {'mode': 'mock'}}])
def test_require_real_rejects_non_real_predictor_results(output):
    result = client('real', predictor=lambda *_: output).post('/predict', json={'params': {'require_real': True}}).json()
    assert result['ok'] is False
    assert result['outputs'] == {}


def test_missing_checkpoint_fails_instead_of_returning_random_module(tmp_path):
    with pytest.raises(FileNotFoundError, match='refusing random initialization'):
        _load_state_dict(torch.nn.Linear(2, 1), tmp_path / 'missing.pt')


def test_partial_checkpoint_cannot_leave_random_parameters(tmp_path):
    path = tmp_path / 'partial.pt'
    torch.save({'weight': torch.ones(1, 2)}, path)
    with pytest.raises(RuntimeError, match='bias'):
        _load_state_dict(torch.nn.Linear(2, 1), path)


def test_complete_checkpoint_is_loaded(tmp_path):
    path = tmp_path / 'complete.pt'
    expected = torch.nn.Linear(2, 1)
    torch.save(expected.state_dict(), path)
    actual = _load_state_dict(torch.nn.Linear(2, 1), path)
    assert torch.equal(actual.weight, expected.weight)
    assert torch.equal(actual.bias, expected.bias)


@pytest.mark.parametrize('inputs', [{}, {'value': 1, 'extra': 2}])
def test_opt_in_input_schema_rejects_invalid_payload_before_computation(tmp_path, inputs):
    schema_path = tmp_path / 'input.schema.json'
    schema_path.write_text(json.dumps({'type': 'object', 'required': ['value'],
                                      'properties': {'value': {'type': 'integer'}}, 'additionalProperties': False}))
    called = []
    app = create_algorithm_app('example', '1.0.0', 'test', lambda *_: called.append(True),
                               input_schema_path=str(schema_path), inference_mode_callable=lambda: 'deterministic')
    result = TestClient(app).post('/predict', json={'inputs': inputs, 'params': {'require_real': True}})
    assert result.status_code == 422
    assert result.json()['ok'] is False
    assert called == []
