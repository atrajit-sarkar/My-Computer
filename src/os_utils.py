from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class OSInfo:
    name: str  # 'Windows' | 'Linux' | 'Darwin'
    shell: str # shell executable
    is_windows: bool


def detect_os() -> OSInfo:
    sys = platform.system()
    if sys == 'Windows':
        return OSInfo(name='Windows', shell='powershell', is_windows=True)
    elif sys == 'Darwin':
        return OSInfo(name='Darwin', shell='/bin/bash', is_windows=False)
    else:
        # Assume Linux/Unix
        return OSInfo(name='Linux', shell='/bin/bash', is_windows=False)


def wrap_command_for_shell(command: str, osinfo: OSInfo) -> list[str]:
    """Return argv list for subprocess based on OS and shell.

    For Windows PowerShell we use: powershell -NoLogo -NoProfile -Command "..."
    For Unix shells we use: /bin/bash -lc "..."
    """
    if osinfo.is_windows:
        return [
            osinfo.shell,
            '-NoLogo',
            '-NoProfile',
            '-Command',
            command,
        ]
    else:
        return [
            osinfo.shell,
            '-lc',
            command,
        ]
