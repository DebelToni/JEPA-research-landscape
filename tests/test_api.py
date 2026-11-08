"""Serving and parsing use separate processes in production and in this suite."""
from pathlib import Path
import subprocess
import sys


def test_agent_api_suite():
    # Thread allocator arenas survive thread shutdown. Keep their virtual-memory
    # high-water mark out of the later parser tests' inherited RLIMIT_AS budget.
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(Path(__file__).with_name('api_cases.py'))],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
