import runpy
from pathlib import Path


def test_st_gnn_benchmark_script_can_import_from_file_entrypoint(monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_st_gnn_runtime.py"
    monkeypatch.setattr("sys.argv", [str(script), "--help"])

    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
