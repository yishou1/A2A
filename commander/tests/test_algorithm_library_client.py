"""算法库 HTTP 客户端与工厂测试。"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from agent.algorithm_library.client import AlgorithmLibraryClient, AlgorithmLibraryError
from agent.algorithm_library.endpoints import default_predict_endpoint
from agent.algorithm_library.factory import create_marl_ppo_scheduler, execution_mode
from agent.algorithm_library.remote_backend import RemoteAlgorithmBackend


class AlgorithmLibraryClientTest(unittest.TestCase):
    def test_default_endpoint(self):
        url = default_predict_endpoint("marl_ppo_task_scheduler")
        self.assertEqual(url, "http://127.0.0.1:9024/predict")

    @patch("agent.algorithm_library.client.urlopen")
    def test_predict_success(self, mock_urlopen: MagicMock):
        payload = {
            "ok": True,
            "outputs": {"sensor_assignments": [], "reattack_plan": [], "algorithm": "mock"},
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        client = AlgorithmLibraryClient({"host": "127.0.0.1"})
        outputs = client.predict("marl_ppo_task_scheduler", {"tracks": [], "detections": [], "frames": []})
        self.assertEqual(outputs["algorithm"], "mock")

    @patch("agent.algorithm_library.client.urlopen")
    def test_predict_failure(self, mock_urlopen: MagicMock):
        payload = {"ok": False, "error": {"code": "ValueError", "message": "bad input"}}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        client = AlgorithmLibraryClient()
        with self.assertRaises(AlgorithmLibraryError):
            client.predict("marl_ppo_task_scheduler", {})


class AlgorithmLibraryFactoryTest(unittest.TestCase):
    def test_execution_mode_default(self):
        self.assertEqual(execution_mode({}), "algorithm_library")

    def test_execution_mode_in_process_env(self):
        with patch.dict(os.environ, {"TIA_EXECUTION_MODE": "in_process"}):
            self.assertEqual(execution_mode({}), "in_process")

    def test_create_remote_scheduler(self):
        with patch.dict(os.environ, {"TIA_EXECUTION_MODE": "algorithm_library"}):
            backend = create_marl_ppo_scheduler(use_mock=True, config={"algorithm_library": {"host": "127.0.0.1"}})
        self.assertIsInstance(backend, RemoteAlgorithmBackend)
        self.assertEqual(backend.algorithm_id, "marl_ppo_task_scheduler")

    def test_create_local_scheduler(self):
        with patch.dict(os.environ, {"TIA_EXECUTION_MODE": "in_process"}):
            from agent.skills.perception.marl_ppo_scheduler import MARLPPOScheduler

            backend = create_marl_ppo_scheduler(use_mock=True, config={})
        self.assertIsInstance(backend, MARLPPOScheduler)


if __name__ == "__main__":
    unittest.main()
