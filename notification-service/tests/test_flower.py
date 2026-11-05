"""The flower compose profile runs `celery flower`: that command only exists if the package is installed."""

import subprocess
import sys


def test_the_flower_command_exists():
    done = subprocess.run(
        [sys.executable, "-m", "celery", "-A", "app.celery_app", "flower", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-500:]
