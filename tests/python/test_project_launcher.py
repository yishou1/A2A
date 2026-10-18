"""Run the Windows launcher against a fake Docker CLI; no containers/downloads."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"

FAKE_DOCKER = r'''
$ErrorActionPreference = 'Stop'
Add-Content -LiteralPath $env:A2A_TEST_LOG -Value (ConvertTo-Json -Compress -InputObject @($args)) -Encoding UTF8
if ($args[0] -eq 'info') {
    if ($env:A2A_TEST_DAEMON_OFF -eq '1') { exit 1 }
    if ($args[-1] -eq '{{.OSType}}') { Write-Output 'linux' }
    else { Write-Output $env:A2A_TEST_MEMORY }
    exit 0
}
if ($args -contains 'version') { Write-Output 'Docker Compose version v2.32.0'; exit 0 }
if ($args -contains 'config') {
    $amos = if ($env:AMOS_HOST_PORT) { $env:AMOS_HOST_PORT } else { $env:A2A_TEST_AMOS_PORT }
    $gateway = if ($env:A2A_GATEWAY_HOST_PORT) { $env:A2A_GATEWAY_HOST_PORT } else { $env:A2A_TEST_GATEWAY_PORT }
    @{ services = @{
        amos = @{ ports = @(@{ published = "$amos"; target = 5000 }) }
        'a2a-core' = @{ ports = @(@{ published = "$gateway"; target = 8030 }); environment = @{ RTDETR_USE_MOCK = $(if ($env:A2A_TEST_REAL_RTDETR) { '0' } else { '1' }) } }
    }} | ConvertTo-Json -Depth 6 -Compress
    exit 0
}
if ($args -contains 'port') {
    if ($env:A2A_TEST_OWNED_PORT) { Write-Output "127.0.0.1:$($env:A2A_TEST_OWNED_PORT)"; exit 0 }
    if ($env:A2A_TEST_STARTED -eq '1') {
        $port = if ($env:AMOS_HOST_PORT) { $env:AMOS_HOST_PORT } else { '5000' }
        Write-Output "0.0.0.0:$port"; exit 0
    }
    exit 1
}
if (($env:A2A_TEST_FAIL -eq 'build' -and $args -contains 'build') -or
    ($env:A2A_TEST_FAIL -eq 'health' -and $args[-1] -eq 'full')) { exit 7 }
exit 0
'''


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class ProjectLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.area = ROOT / ".runtime" / "launcher-tests"
        self.area.mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="case-", dir=self.area))
        self.repo = self.temp / "中文 部署目录"
        self.repo.mkdir()
        # Copy just the launcher and its required file set into a relocated fixture.
        for relative in (
            "scripts/project.ps1", "scripts/docker-common.ps1", "scripts/docker-start.ps1",
            "scripts/docker-stop.ps1", "scripts/docker-compose.ps1", "启动项目.bat",
            "compose.yaml", "compose.gpu.yaml", "scripts/docker/Dockerfile",
            "scripts/docker/Dockerfile.qwen-init", "scripts/docker/healthcheck.py", "scripts/start.sh",
            "algorithmrepo/web/package-lock.json", "commander/CMakeLists.txt",
        ):
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        for relative in (
            "algorithmrepo/models/checkpoints/edl_head_s.safetensors",
            "algorithmrepo/models/checkpoints/mamba_fusion_s.safetensors",
            "algorithmrepo/models/checkpoints/marl_ppo_scheduler_s.safetensors",
            "algorithmrepo/models/checkpoints/supcon_meta_s.safetensors",
            "algorithmrepo/models/track_threat/st_gnn_ship_kaggle_v1/model.ts",
            "algorithmrepo/knowledge_bases/newport_roe_handbook_2022/indexes/newport-roe-handbook-2022-v1/qwen3-embedding_0.6b/index_manifest.json",
        ):
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture", encoding="utf-8")
        index = self.repo / 'algorithmrepo/knowledge_bases/newport_roe_handbook_2022/indexes/newport-roe-handbook-2022-v1/qwen3-embedding_0.6b'
        for relative in ('graph.pickle', 'chunk_embeddings/vdb_chunk.parquet',
                         'entity_embeddings/vdb_entity.parquet', 'fact_embeddings/vdb_fact.parquet'):
            target = index / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture', encoding='ascii')
        self.bin = self.temp / "bin"
        self.bin.mkdir()
        (self.bin / "fake-docker.ps1").write_text(FAKE_DOCKER, encoding="ascii")
        (self.bin / "docker.cmd").write_text(
            f'@echo off\n"{POWERSHELL}" -NoProfile -ExecutionPolicy Bypass -File "%~dp0fake-docker.ps1" %*\nexit /b %ERRORLEVEL%\n',
            encoding="ascii",
        )
        self.log = self.temp / "docker.jsonl"
        self.env = dict(os.environ)
        for key in ("AMOS_HOST_PORT", "A2A_GATEWAY_HOST_PORT", "A2A_DOCKER_PATH"):
            self.env.pop(key, None)
        self.env.update({
            "PATH": str(self.bin) + os.pathsep + self.env["PATH"],
            "TEMP": str(self.temp), "TMP": str(self.temp),
            "A2A_TEST_LOG": str(self.log), "A2A_TEST_MEMORY": str(16 * 1024**3),
            "A2A_TEST_AMOS_PORT": str(free_port()), "A2A_TEST_GATEWAY_PORT": str(free_port()),
        })

    def tearDown(self) -> None:
        # Only delete this test-owned, resolved directory inside .runtime/launcher-tests.
        assert self.temp.resolve().is_relative_to(self.area.resolve())
        shutil.rmtree(self.temp)

    def run_launcher(self, action: str, *options: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(self.repo / "scripts/project.ps1"), "-Action", action, *options],
            cwd=self.temp, env=self.env, capture_output=True, text=True, errors="replace", timeout=90,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line.lstrip("\ufeff")) for line in self.log.read_text(encoding="utf-8-sig").splitlines()]

    def test_preflight_in_chinese_path_has_no_container_mutations(self) -> None:
        result = self.run_launcher("Check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Preflight passed", result.stdout)
        self.assertFalse(any(set(call) & {"build", "up", "down", "stop", "exec"} for call in self.calls()))

    def test_start_forwards_gpu_and_ports_and_verifies_full_health(self) -> None:
        self.env["A2A_TEST_STARTED"] = "1"
        port = str(free_port())
        result = self.run_launcher("Start", "-Gpu", "-NoBrowser", "-AmosPort", port)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        build = next(call for call in calls if "build" in call)
        self.assertTrue(any("compose.gpu.yaml" in part for part in build))
        build_context = build[build.index("--project-directory") + 1]
        self.assertTrue(build_context.isascii())
        self.assertFalse(Path(build_context).exists())  # junction removed, source preserved
        self.assertTrue((self.repo / "compose.yaml").is_file())
        self.assertEqual(calls[-2][-4:], ["a2a-core", "python", "scripts/docker/healthcheck.py", "full"])
        self.assertIn(f"Ready: http://127.0.0.1:{port}/", result.stdout)
        self.assertIn("RT-DETR mock=1", result.stdout)

    def test_build_failure_never_starts_containers_or_reports_ready(self) -> None:
        self.env["A2A_TEST_FAIL"] = "build"
        result = self.run_launcher("Start", "-NoBrowser")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Ready:", result.stdout)
        self.assertTrue(any("build" in call for call in self.calls()), result.stdout + result.stderr)
        self.assertFalse(any("up" in call for call in self.calls()))

    def test_unhealthy_full_backend_never_reports_success(self) -> None:
        self.env["A2A_TEST_FAIL"] = "health"
        result = self.run_launcher("Start", "-NoBrowser")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Ready:", result.stdout)
        self.assertTrue(any(call[-1] == "full" for call in self.calls()))

    def test_low_memory_and_stopped_daemon_are_nonmutating_failures(self) -> None:
        self.env["A2A_TEST_MEMORY"] = str(8 * 1024**3)
        self.assertNotEqual(self.run_launcher("Check").returncode, 0)
        self.env["A2A_TEST_DAEMON_OFF"] = "1"
        self.assertNotEqual(self.run_launcher("Check").returncode, 0)
        self.assertFalse(any(set(call) & {"build", "up", "desktop"} for call in self.calls()))

    def test_foreign_port_is_rejected_but_own_running_port_is_reusable(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = str(listener.getsockname()[1])
            self.env["A2A_TEST_AMOS_PORT"] = port
            result = self.run_launcher("Check")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Host port", result.stderr)
            self.env["A2A_TEST_OWNED_PORT"] = port
            result = self.run_launcher("Check")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stop_preserves_volumes_and_status_requires_full_health(self) -> None:
        result = self.run_launcher("Stop")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any(call[-1] == "down" and "--volumes" not in call and "-v" not in call for call in self.calls()))
        self.env["A2A_TEST_FAIL"] = "health"
        self.assertNotEqual(self.run_launcher("Status").returncode, 0)

    def test_missing_file_fails_before_building(self) -> None:
        (self.repo / "algorithmrepo/web/package-lock.json").unlink()
        result = self.run_launcher("Check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incomplete deployment", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_missing_real_detector_weights_fail_before_building(self) -> None:
        self.env['A2A_TEST_REAL_RTDETR'] = '1'
        result = self.run_launcher('Check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('real RT-DETR checkpoint is missing', result.stderr)
        self.assertFalse(any(set(call) & {'build', 'up', 'down', 'stop'} for call in self.calls()))

    def test_lfs_pointer_is_rejected_before_building(self) -> None:
        asset = self.repo / 'algorithmrepo/models/checkpoints/edl_head_s.safetensors'
        asset.write_text('version https://git-lfs.github.com/spec/v1\noid sha256:example\nsize 123\n', encoding='ascii')
        result = self.run_launcher('Check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Git LFS pointer', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_double_click_entry_uses_its_own_directory(self) -> None:
        self.env['A2A_TEST_STARTED'] = '1'
        cmd = os.environ.get('COMSPEC', 'C:/Windows/System32/cmd.exe')
        bat = self.repo / '启动项目.bat'
        result = subprocess.run(
            f'"{cmd}" /d /c ""{bat}" -NoBrowser"', cwd=self.temp, env=self.env,
            capture_output=True, text=True, errors='replace', timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Ready:', result.stdout)
        self.assertTrue(any(call[-1] == 'full' for call in self.calls()))


class FullHealthcheckTests(unittest.TestCase):
    def test_mock_metadata_cannot_pass_real_model_health(self) -> None:
        spec = importlib.util.spec_from_file_location('launcher_real_health', ROOT / 'scripts/docker/healthcheck.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, 'urlopen') as upstream, patch.dict(os.environ, {'RTDETR_USE_MOCK': '0'}):
            response = upstream.return_value.__enter__.return_value
            response.status = 200
            response.read.side_effect = [
                b'{"ok":true,"model_loaded":true,"algorithm_id":"battlefield_rtdetr_detector"}',
                b'{"inference_mode":"mock","num_classes":97}',
            ]
            with self.assertRaisesRegex(RuntimeError, 'required real 97-class model'):
                module.probe_rtdetr()

    def test_rtdetr_not_loaded_is_not_a_healthy_full_stack(self) -> None:
        spec = importlib.util.spec_from_file_location("launcher_rtdetr_health", ROOT / "scripts/docker/healthcheck.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, 'urlopen') as upstream, patch.dict(os.environ, {'AMOS_RTDETR_ENDPOINT': 'http://a2a-core:9020'}):
            response = upstream.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{"ok":false,"model_loaded":false,"algorithm_id":"battlefield_rtdetr_detector"}'
            with self.assertRaisesRegex(RuntimeError, 'RT-DETR is not ready'):
                module.probe_rtdetr()
            upstream.assert_called_once_with('http://a2a-core:9020/health', timeout=10)

    def test_full_health_cannot_pass_with_a_missing_agent(self) -> None:
        spec = importlib.util.spec_from_file_location("launcher_healthcheck", ROOT / "scripts/docker/healthcheck.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def require_agent(port):
            if port == 10205:
                raise RuntimeError("closed-loop agent unavailable")
        with patch.object(module, "probe_port", side_effect=require_agent), patch.object(module.sys, "argv", ["healthcheck.py", "full"]):
            with self.assertRaisesRegex(RuntimeError, "closed-loop agent unavailable"):
                module.main()

    def test_open_port_with_unhealthy_agent_http_is_not_ready(self) -> None:
        spec = importlib.util.spec_from_file_location("launcher_agent_http", ROOT / "scripts/docker/healthcheck.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def unhealthy_http(url):
            if url == 'http://127.0.0.1:10204/health':
                raise RuntimeError('execution agent HTTP 503')
        with patch.object(module, 'probe_port'), patch.object(module, 'probe_http', side_effect=unhealthy_http), patch.object(module.sys, 'argv', ['healthcheck.py', 'full']):
            with self.assertRaisesRegex(RuntimeError, 'execution agent HTTP 503'):
                module.main()


if __name__ == "__main__":
    unittest.main(verbosity=2)
