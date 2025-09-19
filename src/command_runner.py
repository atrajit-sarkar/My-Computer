from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Tuple

from .os_utils import detect_os, wrap_command_for_shell


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


async def run_command(command: str, work_dir: str | None = None, timeout: float | None = None) -> CommandResult:
    osinfo = detect_os()
    argv = wrap_command_for_shell(command, osinfo)
    cwd = work_dir or os.getcwd()

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CommandResult(command=command, exit_code=124, stdout='', stderr='Command timed out')

    stdout = stdout_b.decode(errors='replace') if stdout_b else ''
    stderr = stderr_b.decode(errors='replace') if stderr_b else ''
    return CommandResult(command=command, exit_code=proc.returncode or 0, stdout=stdout, stderr=stderr)


def summarize_output(text: str, limit: int = 1800) -> str:
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.6)]
    tail = text[-int(limit * 0.35) :]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n...\n[omitted {omitted} chars]\n...\n{tail}"
