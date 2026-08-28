"""Regression tests for the file-based scout entrypoints."""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_run_entrypoint_imports_top_level_pipeline_package():
    result = subprocess.run(
        ["uv", "run", "scout/run.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "--workers" in result.stdout
