"""Read-only repository summary for the CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


SKIPPED_DIRECTORIES = {
    ".git",
    ".codefu-panda",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".test_workspaces",
}
INTERNAL_FILES = {"agent_history.md"}
KEY_PATHS = (
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "README.md",
    "tests",
    "src",
    "local_agent",
)
DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
}
LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".md": "Markdown",
    ".toml": "TOML",
    ".json": "JSON",
    ".yml": "YAML",
    ".yaml": "YAML",
}


@dataclass(frozen=True)
class RepoSummary:
    project_root: Path
    language_indicators: list[str]
    key_files: list[str]
    extension_counts: dict[str, int]
    tests_directory_exists: bool
    ci_config_exists: bool
    git_repo_exists: bool
    notes: list[str]

    def to_display_text(self) -> str:
        lines = [
            "Repository Summary",
            "",
            f"Project root: {self.project_root}",
            "Detected language indicators:",
        ]
        if self.language_indicators:
            lines.extend(f"- {item}" for item in self.language_indicators)
        else:
            lines.append("- None detected")

        lines.append("Key files found:")
        if self.key_files:
            lines.extend(f"- {item}" for item in self.key_files)
        else:
            lines.append("- None detected")

        lines.append("File counts by extension:")
        if self.extension_counts:
            for extension, count in sorted(self.extension_counts.items()):
                lines.append(f"- {extension}: {count}")
        else:
            lines.append("- None detected")

        lines.extend(
            [
                f"Tests directory exists: {'yes' if self.tests_directory_exists else 'no'}",
                f"CI config exists: {'yes' if self.ci_config_exists else 'no'}",
                f"Git repo exists: {'yes' if self.git_repo_exists else 'no'}",
                "Safety notes:",
            ]
        )
        if self.notes:
            lines.extend(f"- {item}" for item in self.notes)
        else:
            lines.append("- None")
        return "\n".join(lines)


def summarize_repo(project_root: Path) -> RepoSummary:
    root = project_root.resolve(strict=True)
    extension_counts: dict[str, int] = {}

    for current_root, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current_root)
        kept_dirs: list[str] = []
        for dirname in dirs:
            directory = current_path / dirname
            if dirname in SKIPPED_DIRECTORIES or directory.is_symlink():
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs

        for filename in files:
            if filename in INTERNAL_FILES:
                continue
            file_path = current_path / filename
            if file_path.is_symlink():
                continue
            extension = file_path.suffix.lower() or "[no extension]"
            extension_counts[extension] = extension_counts.get(extension, 0) + 1

    key_files = [item for item in KEY_PATHS if (root / item).exists()]
    language_indicators = _language_indicators(root, extension_counts)
    tests_directory_exists = (root / "tests").is_dir()
    ci_config_exists = _ci_config_exists(root)
    git_repo_exists = (root / ".git").exists()
    notes = _notes(root, dependency_files=key_files)

    return RepoSummary(
        project_root=root,
        language_indicators=language_indicators,
        key_files=key_files,
        extension_counts=extension_counts,
        tests_directory_exists=tests_directory_exists,
        ci_config_exists=ci_config_exists,
        git_repo_exists=git_repo_exists,
        notes=notes,
    )


def _language_indicators(root: Path, extension_counts: dict[str, int]) -> list[str]:
    indicators = sorted(
        {
            language
            for extension, language in LANGUAGE_BY_EXTENSION.items()
            if extension_counts.get(extension, 0) > 0
        }
    )
    if (root / "pyproject.toml").exists():
        indicators.append("Python package detected")
    if (root / "package.json").exists():
        indicators.append("Node package detected")
    return indicators


def _ci_config_exists(root: Path) -> bool:
    return (
        (root / ".github" / "workflows").is_dir()
        or (root / ".gitlab-ci.yml").is_file()
        or (root / ".circleci").is_dir()
    )


def _notes(root: Path, dependency_files: list[str]) -> list[str]:
    notes = [
        "Summary is read-only; uncommitted files are not checked.",
    ]
    if not any((root / filename).exists() for filename in DEPENDENCY_FILES):
        notes.append("No dependency manifest found.")
    if "pyproject.toml" in dependency_files:
        notes.append("Python package detected.")
    return notes
