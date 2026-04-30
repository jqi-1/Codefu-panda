"""Shared data structures for the local coding agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProjectSummary:
    root: Path
    primary_language: str
    secondary_languages: list[str]
    likely_entry_points: list[str]
    file_count: int
    ignored_directory_count: int
    tests_detected: bool
    dependency_files: list[str]
    language_counts: dict[str, int] = field(default_factory=dict)

    def to_display_text(self) -> str:
        lines = [
            "Project Summary",
            "",
            f"Root: {self.root}",
            f"Primary language: {self.primary_language}",
        ]
        if self.secondary_languages:
            lines.append(
                "Secondary languages: " + ", ".join(self.secondary_languages)
            )
        else:
            lines.append("Secondary languages: None detected")

        lines.append("Likely entry points:")
        if self.likely_entry_points:
            lines.extend(f"- {entry}" for entry in self.likely_entry_points)
        else:
            lines.append("- None detected")

        lines.extend(
            [
                f"File count: {self.file_count}",
                f"Ignored directories: {self.ignored_directory_count}",
                f"Tests detected: {'yes' if self.tests_detected else 'no'}",
                "Dependency files:",
            ]
        )
        if self.dependency_files:
            lines.extend(f"- {path}" for path in self.dependency_files)
        else:
            lines.append("- None detected")
        return "\n".join(lines)

    def to_prompt_context(self) -> str:
        language_counts = ", ".join(
            f"{name}: {count}" for name, count in sorted(self.language_counts.items())
        )
        if not language_counts:
            language_counts = "None"
        return "\n".join(
            [
                f"Project root: {self.root}",
                f"Primary language: {self.primary_language}",
                "Secondary languages: "
                + (
                    ", ".join(self.secondary_languages)
                    if self.secondary_languages
                    else "None"
                ),
                "Likely entry points: "
                + (
                    ", ".join(self.likely_entry_points)
                    if self.likely_entry_points
                    else "None detected"
                ),
                f"File count: {self.file_count}",
                f"Ignored directory count: {self.ignored_directory_count}",
                f"Tests detected: {'yes' if self.tests_detected else 'no'}",
                "Dependency files: "
                + (
                    ", ".join(self.dependency_files)
                    if self.dependency_files
                    else "None detected"
                ),
                f"Language counts: {language_counts}",
            ]
        )


@dataclass(frozen=True)
class SuggestionProposal:
    suggestions: list[str]

    def __post_init__(self) -> None:
        if len(self.suggestions) != 2:
            raise ValueError("SuggestionProposal must contain exactly two suggestions")


@dataclass(frozen=True)
class CommandProposal:
    command: str


@dataclass(frozen=True)
class EditProposal:
    diff: str


@dataclass(frozen=True)
class CommandResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    error_message: str = ""


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str = ""
    event_type: str = "COMMAND_BLOCKED"
    tokens: list[str] = field(default_factory=list)
    risky: bool = False
    risk_message: str = ""


@dataclass(frozen=True)
class EditResult:
    ok: bool
    message: str
    event_type: str
    affected_paths: list[str] = field(default_factory=list)
    temp_path: str | None = None
