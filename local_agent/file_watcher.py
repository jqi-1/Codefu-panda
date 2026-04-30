"""Project scanning and conservative text reads."""

from __future__ import annotations

import os
from pathlib import Path

from .logger import AgentLogger
from .models import ProjectSummary


DEFAULT_IGNORED_DIRECTORIES = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".coverage",
    ".mypy_cache",
    ".pytest_cache",
    ".next",
    ".cache",
}

INTERNAL_AGENT_FILES = {"agent_history.md"}

LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".md": "Markdown",
    ".rst": "reStructuredText",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".rs": "Rust",
    ".go": "Go",
    ".css": "CSS",
    ".html": "HTML",
    ".htm": "HTML",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".java": "Java",
    ".c": "C",
    ".h": "C/C++",
    ".cpp": "C++",
    ".cc": "C++",
    ".cs": "C#",
}

ENTRY_POINT_CANDIDATES = {
    "main.py",
    "app.py",
    "src/main.py",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "Makefile",
}

DEPENDENCY_FILES = {
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
}

TEST_FILE_SUFFIXES = (
    ".test.js",
    ".spec.js",
    "_test.go",
)


class SandboxError(RuntimeError):
    """Raised when a read would leave the project root."""


class ProjectScanner:
    def __init__(
        self,
        project_root: Path,
        ignored_directories: set[str] | None = None,
        logger: AgentLogger | None = None,
    ) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.ignored_directories = set(DEFAULT_IGNORED_DIRECTORIES)
        if ignored_directories:
            self.ignored_directories.update(ignored_directories)
        self.logger = logger

    def scan(self) -> ProjectSummary:
        file_count = 0
        ignored_directory_count = 0
        language_counts: dict[str, int] = {}
        likely_entry_points: list[str] = []
        dependency_files: list[str] = []
        tests_detected = False

        for current_root, dirs, files in os.walk(self.project_root, followlinks=False):
            current_path = Path(current_root)
            kept_dirs: list[str] = []
            for dirname in dirs:
                dir_path = current_path / dirname
                if dirname in self.ignored_directories:
                    ignored_directory_count += 1
                    continue
                if dir_path.is_symlink():
                    ignored_directory_count += 1
                    continue
                try:
                    resolved_dir = dir_path.resolve(strict=True)
                except OSError:
                    ignored_directory_count += 1
                    continue
                if not _is_relative_to(resolved_dir, self.project_root):
                    ignored_directory_count += 1
                    continue
                kept_dirs.append(dirname)
            dirs[:] = kept_dirs

            for filename in files:
                if filename in INTERNAL_AGENT_FILES:
                    continue
                file_path = current_path / filename
                try:
                    resolved_file = file_path.resolve(strict=True)
                except OSError:
                    continue
                if not _is_relative_to(resolved_file, self.project_root):
                    continue

                file_count += 1
                relative_path = file_path.relative_to(self.project_root).as_posix()
                suffix = file_path.suffix.lower()
                language = LANGUAGE_BY_EXTENSION.get(suffix)
                if language:
                    language_counts[language] = language_counts.get(language, 0) + 1
                if relative_path in ENTRY_POINT_CANDIDATES:
                    likely_entry_points.append(relative_path)
                if file_path.name in DEPENDENCY_FILES:
                    dependency_files.append(relative_path)
                if _looks_like_test(relative_path):
                    tests_detected = True

        sorted_languages = sorted(
            language_counts.items(), key=lambda item: (-item[1], item[0])
        )
        primary_language = sorted_languages[0][0] if sorted_languages else "Unknown"
        secondary_languages = [name for name, _ in sorted_languages[1:]]

        return ProjectSummary(
            root=self.project_root,
            primary_language=primary_language,
            secondary_languages=secondary_languages,
            likely_entry_points=sorted(likely_entry_points),
            file_count=file_count,
            ignored_directory_count=ignored_directory_count,
            tests_detected=tests_detected,
            dependency_files=sorted(dependency_files),
            language_counts=language_counts,
        )

    def read_text_file(self, relative_path: str) -> str:
        path = self._safe_path(relative_path)
        if _appears_binary(path):
            if self.logger:
                self.logger.log("READ", "Skipped binary-looking file", path=path)
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "binary-looking file")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            if self.logger:
                self.logger.log("ERROR", "Could not decode text file", path=path)
            raise
        if self.logger:
            self.logger.log("READ", "Read text file", path=path)
        return text

    def _safe_path(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=candidate.exists())
        else:
            rooted = self.project_root / candidate
            resolved = rooted.resolve(strict=rooted.exists())
        if not _is_relative_to(resolved, self.project_root):
            raise SandboxError(f"Read denied outside project root: {resolved}")
        return resolved


def _looks_like_test(relative_path: str) -> bool:
    parts = relative_path.split("/")
    if any(part in {"test", "tests", "__tests__"} for part in parts):
        return True
    name = parts[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or any(name.endswith(suffix) for suffix in TEST_FILE_SUFFIXES)
    )


def _appears_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:8192]
    except OSError:
        return True
    return b"\0" in sample


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
