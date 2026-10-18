"""Exercise real POSIX process maintenance without any application algorithms."""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform == 'win32', reason='Run in the Linux deployment image')
def test_intentional_stop_does_not_trigger_crash_supervisor(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(ROOT / 'scripts/common.sh', scripts / 'common.sh')
    script = r'''
set -euo pipefail
source "$1/scripts/common.sh"
setsid python -c 'import signal,time; signal.signal(signal.SIGTERM, lambda *_: (time.sleep(0.4),exit(0))); print("ready",flush=True); time.sleep(30)' > "$1/worker.ready" &
worker=$!
trap 'kill -- -$worker 2>/dev/null || true' EXIT
printf '%s\n' "$worker" > "$PID_DIR/worker.pid"
while ! test -s "$1/worker.ready"; do sleep 0.01; done
(
  while [[ ! -e "$1/maintenance.done" ]]; do
    if managed_service_failed "$PID_DIR/worker.pid"; then touch "$1/false-crash"; break; fi
    sleep 0.005
  done
) &
monitor=$!
stop_service worker
touch "$1/maintenance.done"
wait "$monitor"
test ! -e "$1/false-crash"
test ! -e "$PID_DIR/worker.pid"
'''
    result = subprocess.run(['bash', '-c', script, 'maintenance-test', str(tmp_path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform == 'win32', reason='Run in the Linux deployment image')
def test_unexpected_exit_is_still_detected(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copyfile(ROOT / 'scripts/common.sh', scripts / 'common.sh')
    script = r'''
set -euo pipefail
source "$1/scripts/common.sh"
setsid sleep 30 &
worker=$!
trap 'kill -- -$worker 2>/dev/null || true' EXIT
printf '%s\n' "$worker" > "$PID_DIR/worker.pid"
kill -KILL "$worker"
wait "$worker" || true
managed_service_failed "$PID_DIR/worker.pid"
'''
    result = subprocess.run(['bash', '-c', script, 'crash-test', str(tmp_path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
