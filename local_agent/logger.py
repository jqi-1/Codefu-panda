"""Append-only Markdown logger for the local coding agent."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


class LoggerError(RuntimeError):
    """Raised when required logging cannot be performed safely."""


class AgentLogger:
    """Append-only logger that refuses to write outside the project root."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.log_path = self.project_root / "agent_history.md"
        self._assert_log_path_safe()

    def _assert_log_path_safe(self) -> None:
        try:
            if self.log_path.exists() or self.log_path.is_symlink():
                resolved_log_path = self.log_path.resolve(strict=True)
            else:
                resolved_log_path = self.log_path.resolve(strict=False)
            self.project_root.resolve(strict=True)
        except OSError as exc:
            raise LoggerError(f"Cannot resolve log path safely: {exc}") from exc

        if not _is_relative_to(resolved_log_path, self.project_root):
            raise LoggerError(
                f"Refusing to write log outside project root: {resolved_log_path}"
            )

    def log(self, event_type: str, description: str, **fields: Any) -> None:
        self._assert_log_path_safe()
        timestamp = datetime.now().isoformat(timespec="seconds")
        entry = [
            f"## {timestamp} - {event_type}",
            "",
            f"- Working directory: `{self.project_root}`",
            f"- Description: {description}",
        ]

        block_fields = {
            "stdout",
            "stderr",
            "diff",
            "command_output",
            "prompt",
            "raw_output",
            "error_trace",
        }
        for key, value in fields.items():
            if value is None:
                continue
            label = key.replace("_", " ").title()
            if key in block_fields:
                entry.extend(["", f"### {label}", "", _fence(str(value))])
            else:
                entry.append(f"- {label}: `{value}`")
        entry.append("")

        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(entry))
            handle.write("\n")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _fence(content: str) -> str:
    max_run = 0
    current = 0
    for char in content:
        if char == "`":
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    fence = "`" * max(3, max_run + 1)
    return f"{fence}text\n{content}\n{fence}"
