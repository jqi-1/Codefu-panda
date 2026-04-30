"""Subprocess execution with shell=False and structured results."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .models import CommandResult


DEFAULT_TIMEOUT_SECONDS = 30


class CommandRunner:
    def __init__(self, project_root: Path, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
        self.project_root = project_root.resolve(strict=True)
        self.timeout_seconds = timeout_seconds

    def run(self, command: str, tokens: list[str]) -> CommandResult:
        try:
            completed = subprocess.run(
                tokens,
                cwd=self.project_root,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            return CommandResult(
                command=command,
                stdout=completed.stdout,
                stderr=completed.stderr,
                exit_code=completed.returncode,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _coerce_output(exc.stdout)
            stderr = _coerce_output(exc.stderr)
            return CommandResult(
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=None,
                timed_out=True,
                error_message=f"Command timed out after {self.timeout_seconds} seconds",
            )
        except OSError as exc:
            return CommandResult(
                command=command,
                stdout="",
                stderr="",
                exit_code=None,
                timed_out=False,
                error_message=str(exc),
            )


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
