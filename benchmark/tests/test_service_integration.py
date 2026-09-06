"""Run offline service checks when the sibling service environment is available."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "script", ["check_service.py", "service_regressions.py", "calibrate_inspect.py"]
)
def test_sibling_service(script):
    root = Path(__file__).resolve().parents[1]
    interpreter = (
        root.parent / "service/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not interpreter.is_file():
        pytest.skip("Install sibling service environment, or run the offline tools with uv")
    completed = subprocess.run(
        [str(interpreter), str(root / "tools" / script)],
        cwd=root,
        env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
