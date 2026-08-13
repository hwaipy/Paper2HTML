from __future__ import annotations

import os
import subprocess

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "src.converter.cli",
        "src.converter.quality",
        "src.converter.golden",
        "src.validator.cli",
    ],
)
def test_documented_module_commands_import_in_clean_environment(module: str) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        ["uv", "run", "python", "-m", module, "--help"],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
