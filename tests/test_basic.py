import asyncio
import platform

from src.os_utils import detect_os
from src.command_runner import run_command


def test_detect_os():
    info = detect_os()
    assert info.name in ("Windows", "Linux", "Darwin")


def test_run_command_echo():
    # Prepare an OS-agnostic echo
    msg = "hello"
    if platform.system() == "Windows":
        cmd = f'Write-Output "{msg}"'
    else:
        cmd = f'echo "{msg}"'

    result = asyncio.get_event_loop().run_until_complete(run_command(cmd, timeout=10))
    assert result.exit_code == 0
    assert "hello" in result.stdout
