"""Deterministic permission and sandbox enforcement."""

from __future__ import annotations

import os
import re
import shlex
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .logger import AgentLogger
from .models import EditResult, ValidationResult


DEFAULT_ALLOWED_COMMANDS = {
    "git",
    "npm",
    "npx",
    "yarn",
    "pnpm",
    "python",
    "pytest",
    "flake8",
    "eslint",
    "prettier",
    "mypy",
    "black",
    "isort",
    "go",
    "cargo",
    "make",
}

FORBIDDEN_SHELL_MARKERS = (">", "<", "|", "&", ";", "$(", "`")
FORBIDDEN_RAW_PATTERNS = ("rm -rf", "git push --force", ":(){:|:&};")
PATH_VALUE_OPTIONS = {
    "-C",
    "--cwd",
    "--config",
    "--file",
    "--input",
    "--output",
    "--path",
    "--directory",
    "--dir",
    "--project",
    "--cache-dir",
    "--root",
}
NON_PATH_VALUE_OPTIONS = {
    "-m",
    "--message",
    "-k",
    "--keyword",
    "--grep",
    "--author",
    "-b",
    "--branch",
}
PATH_VALUE_PREFIXES = tuple(option + "=" for option in PATH_VALUE_OPTIONS if option.startswith("--"))


@dataclass
class DiffFile:
    old_path: str
    new_path: str
    hunks: list[list[str]]


class PermissionManager:
    def __init__(
        self,
        project_root: Path,
        logger: AgentLogger,
        allowed_commands: set[str] | None = None,
    ) -> None:
        self.project_root = project_root.resolve(strict=True)
        self.logger = logger
        self.allowed_commands = set(DEFAULT_ALLOWED_COMMANDS)
        if allowed_commands:
            self.allowed_commands.update(allowed_commands)

    def confirm(
        self,
        prompt: str,
        input_func=input,
        output_func=print,
    ) -> bool:
        while True:
            response = input_func(prompt + " ").strip()
            lowered = response.lower()
            if lowered == "yes":
                return True
            if lowered == "no":
                return False
            output_func("Please answer exactly yes or no.")
            self.logger.log(
                "INVALID_CONFIRMATION",
                "User entered invalid confirmation response",
                response=response,
            )

    def validate_command(self, command: str) -> ValidationResult:
        if "\n" in command or "\r" in command:
            return ValidationResult(
                False,
                "Blocked: command must be a single line.",
                "COMMAND_BLOCKED",
            )

        destructive = self._detect_destructive_raw_pattern(command)
        if destructive:
            return ValidationResult(False, destructive, "BLOCKED_DESTRUCTIVE_ACTION")

        for marker in FORBIDDEN_SHELL_MARKERS:
            if marker in command:
                return ValidationResult(
                    False,
                    f"Blocked: shell metacharacter or construct `{marker}` is not allowed.",
                    "COMMAND_BLOCKED",
                )

        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return ValidationResult(
                False,
                f"Blocked: command could not be parsed safely: {exc}",
                "COMMAND_BLOCKED",
            )
        if not tokens:
            return ValidationResult(False, "Blocked: empty command.", "COMMAND_BLOCKED")

        base = tokens[0]
        if base not in self.allowed_commands:
            return ValidationResult(
                False,
                f"Blocked: `{base}` is not in the command whitelist.",
                "COMMAND_BLOCKED",
            )

        blocked = self._detect_blocked_token_pattern(tokens)
        if blocked:
            return ValidationResult(False, blocked, "BLOCKED_DESTRUCTIVE_ACTION")

        path_error = self._validate_command_paths(tokens)
        if path_error:
            return ValidationResult(False, path_error, "SANDBOX_DENIED")

        return ValidationResult(True, tokens=tokens, risky=self._is_risky(tokens))

    def apply_unified_diff(self, diff_text: str) -> EditResult:
        try:
            parsed = _parse_unified_diff(diff_text)
        except ValueError as exc:
            result = EditResult(False, str(exc), "EDIT_FAILED")
            self.logger.log("EDIT_FAILED", result.message, diff=diff_text)
            return result

        if len(parsed) != 1:
            result = EditResult(
                False,
                "Only one file may be edited per unified diff in v0.",
                "EDIT_FAILED",
            )
            self.logger.log("EDIT_FAILED", result.message, diff=diff_text)
            return result

        diff_file = parsed[0]
        if diff_file.new_path == "/dev/null":
            result = EditResult(
                False, "File deletion is forbidden in v0.", "BLOCKED_DESTRUCTIVE_ACTION"
            )
            self.logger.log(result.event_type, result.message, diff=diff_text)
            return result

        try:
            target = self._resolve_diff_path(diff_file.new_path)
            if diff_file.old_path != "/dev/null":
                old_target = self._resolve_diff_path(diff_file.old_path)
                if old_target != target:
                    raise ValueError("Renames and multi-path edits are forbidden in v0.")
        except ValueError as exc:
            result = EditResult(False, str(exc), "SANDBOX_DENIED")
            self.logger.log(result.event_type, result.message, diff=diff_text)
            return result

        affected = [str(target)]
        if not target.parent.exists():
            result = EditResult(
                False,
                "Parent directory must already exist for file edits.",
                "EDIT_FAILED",
                affected,
            )
            self.logger.log(result.event_type, result.message, affected_paths=affected)
            return result
        if target.exists() and target.is_dir():
            result = EditResult(False, "Directory editing is forbidden.", "EDIT_FAILED", affected)
            self.logger.log(result.event_type, result.message, affected_paths=affected)
            return result
        if target.exists() and _appears_binary(target):
            result = EditResult(False, "Binary file editing is forbidden.", "EDIT_FAILED", affected)
            self.logger.log(result.event_type, result.message, affected_paths=affected)
            return result
        if diff_file.old_path == "/dev/null" and target.exists():
            result = EditResult(False, "New-file diff target already exists.", "CONFLICT", affected)
            self.logger.log(result.event_type, result.message, affected_paths=affected)
            return result

        try:
            original_text = target.read_text(encoding="utf-8") if target.exists() else ""
        except UnicodeDecodeError:
            result = EditResult(False, "Target file is not valid UTF-8 text.", "EDIT_FAILED", affected)
            self.logger.log(result.event_type, result.message, affected_paths=affected)
            return result
        newline = _detect_newline(original_text)
        original_lines = original_text.splitlines()

        try:
            new_lines = _apply_hunks(original_lines, diff_file.hunks)
        except ValueError as exc:
            result = EditResult(False, str(exc), "CONFLICT", affected)
            self.logger.log(result.event_type, result.message, affected_paths=affected, diff=diff_text)
            return result

        new_text = newline.join(new_lines)
        if new_lines:
            new_text += newline

        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                text=True,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(new_text)
                    handle.flush()
                    os.fsync(handle.fileno())
                if target.exists():
                    mode = stat.S_IMODE(target.stat().st_mode)
                    os.chmod(temp_path, mode)
                os.replace(temp_path, target)
            except Exception:
                if temp_path and Path(temp_path).exists():
                    try:
                        Path(temp_path).unlink()
                    except OSError:
                        pass
                raise
        except OSError as exc:
            result = EditResult(False, f"Edit failed: {exc}", "EDIT_FAILED", affected, temp_path)
            self.logger.log(
                result.event_type,
                result.message,
                affected_paths=affected,
                temp_path=temp_path,
                diff=diff_text,
            )
            return result

        result = EditResult(True, "Edit applied.", "EDIT_APPLIED", affected, temp_path)
        self.logger.log(
            result.event_type,
            result.message,
            affected_paths=affected,
            temp_path=temp_path,
            os_replace_succeeded=True,
            diff=diff_text,
        )
        return result

    def _detect_destructive_raw_pattern(self, command: str) -> str:
        lowered = command.lower()
        for pattern in FORBIDDEN_RAW_PATTERNS:
            if pattern in lowered:
                return f"Blocked: destructive pattern `{pattern}` is not allowed."
        return ""

    def _detect_blocked_token_pattern(self, tokens: list[str]) -> str:
        base = tokens[0]
        if base == "git":
            if len(tokens) >= 2 and tokens[1] in {"rm", "clean"}:
                return f"Blocked: `git {tokens[1]}` is destructive in v0."
            if len(tokens) >= 3 and tokens[1] == "reset" and "--hard" in tokens[2:]:
                return "Blocked: `git reset --hard` is destructive in v0."
            if len(tokens) >= 3 and tokens[1] == "remote" and tokens[2] not in {
                "-v",
                "show",
                "get-url",
            }:
                return "Blocked: changing Git remotes is forbidden in v0."
        if base == "python" and len(tokens) >= 2 and tokens[1] in {"-c", "-"}:
            return "Blocked: inline Python execution is not allowed in v0."
        if _is_global_install(tokens):
            return "Blocked: global or user-level package installation is forbidden."
        return ""

    def _validate_command_paths(self, tokens: list[str]) -> str:
        expect_path = False
        skip_next_non_path = False
        for token in tokens[1:]:
            if skip_next_non_path:
                skip_next_non_path = False
                continue
            if expect_path:
                error = self._validate_path_token(token)
                if error:
                    return error
                expect_path = False
                continue
            if token in PATH_VALUE_OPTIONS:
                expect_path = True
                continue
            if token in NON_PATH_VALUE_OPTIONS:
                skip_next_non_path = True
                continue
            matched_prefix = next(
                (prefix for prefix in PATH_VALUE_PREFIXES if token.startswith(prefix)),
                None,
            )
            if matched_prefix:
                error = self._validate_path_token(token[len(matched_prefix) :])
                if error:
                    return error
                continue
            if token.startswith("-"):
                continue
            if self._looks_like_path(token):
                error = self._validate_path_token(token)
                if error:
                    return error
        if expect_path:
            return "Blocked: path option is missing its path value."
        return ""

    def _looks_like_path(self, token: str) -> bool:
        if token in {".", ".."}:
            return True
        if "/" in token or "\\" in token:
            return True
        candidate = Path(token)
        if candidate.is_absolute():
            return True
        if (self.project_root / candidate).exists():
            return True
        suffix = candidate.suffix.lower()
        return bool(suffix and re.match(r"^\.[a-z0-9_+-]+$", suffix))

    def _validate_path_token(self, token: str) -> str:
        if not token:
            return "Blocked: empty path value."
        candidate = Path(token)
        try:
            if candidate.is_absolute():
                resolved = candidate.resolve(strict=candidate.exists())
            else:
                resolved = (self.project_root / candidate).resolve(
                    strict=(self.project_root / candidate).exists()
                )
        except OSError as exc:
            return f"Blocked: path could not be resolved safely: {exc}"
        if not _is_relative_to(resolved, self.project_root):
            return f"Blocked: path escapes project root: {token}"
        return ""

    def _resolve_diff_path(self, diff_path: str) -> Path:
        stripped = _strip_diff_prefix(diff_path)
        if not stripped:
            raise ValueError("Diff path is empty.")
        candidate = Path(stripped)
        if candidate.is_absolute():
            target = candidate.resolve(strict=candidate.exists())
        else:
            target = (self.project_root / candidate).resolve(
                strict=(self.project_root / candidate).exists()
            )
        if not _is_relative_to(target, self.project_root):
            raise ValueError(f"Diff path escapes project root: {diff_path}")
        return target

    def _is_risky(self, tokens: list[str]) -> bool:
        base = tokens[0]
        if base == "npm" and len(tokens) >= 2 and tokens[1] in {
            "install",
            "i",
            "add",
            "ci",
            "update",
            "uninstall",
            "remove",
        }:
            return True
        if base == "pnpm" and len(tokens) >= 2 and tokens[1] in {
            "install",
            "add",
            "update",
            "remove",
        }:
            return True
        if base == "yarn" and len(tokens) >= 2 and tokens[1] in {
            "install",
            "add",
            "upgrade",
            "remove",
        }:
            return True
        if base == "pip" and len(tokens) >= 2 and tokens[1] == "install":
            return True
        if base == "python" and tokens[1:4] == ["-m", "pip", "install"]:
            return True
        if base == "make" and len(tokens) >= 2 and tokens[1] == "clean":
            return True
        return False


def _is_global_install(tokens: list[str]) -> bool:
    base = tokens[0]
    if base == "npm" and len(tokens) >= 2 and tokens[1] in {"install", "i", "add"}:
        return "-g" in tokens or "--global" in tokens
    if base == "pnpm" and len(tokens) >= 2 and tokens[1] in {"install", "add"}:
        return "-g" in tokens or "--global" in tokens
    if base == "yarn":
        return (len(tokens) >= 3 and tokens[1:3] == ["global", "add"]) or "--global" in tokens
    if base == "pip" and len(tokens) >= 2 and tokens[1] == "install":
        return "--user" in tokens
    if base == "python" and tokens[1:4] == ["-m", "pip", "install"]:
        return "--user" in tokens
    if base == "cargo" and len(tokens) >= 2 and tokens[1] == "install":
        return True
    return False


def _parse_unified_diff(diff_text: str) -> list[DiffFile]:
    lines = diff_text.splitlines()
    files: list[DiffFile] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            raise ValueError("Unified diff must start each file with `--- `.")
        old_path = lines[index][4:].strip().split("\t", 1)[0]
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("Unified diff missing `+++ ` path.")
        new_path = lines[index][4:].strip().split("\t", 1)[0]
        index += 1
        hunks: list[list[str]] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            if not lines[index].startswith("@@ "):
                raise ValueError("Unified diff missing hunk header.")
            hunk = [lines[index]]
            index += 1
            while index < len(lines) and not lines[index].startswith("@@ ") and not lines[
                index
            ].startswith("--- "):
                line = lines[index]
                if line and line[0] not in {" ", "+", "-", "\\"}:
                    raise ValueError("Unified diff contains an invalid hunk line.")
                hunk.append(line)
                index += 1
            hunks.append(hunk)
        if not hunks:
            raise ValueError("Unified diff must contain at least one hunk.")
        files.append(DiffFile(old_path=old_path, new_path=new_path, hunks=hunks))
    if not files:
        raise ValueError("Empty unified diff.")
    return files


def _apply_hunks(original_lines: list[str], hunks: list[list[str]]) -> list[str]:
    output: list[str] = []
    cursor = 0
    for hunk in hunks:
        match = re.match(
            r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
            r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@",
            hunk[0],
        )
        if not match:
            raise ValueError("Invalid hunk header.")
        old_start = int(match.group("old_start"))
        old_index = max(old_start - 1, 0)
        if old_index < cursor:
            raise ValueError("Overlapping hunks are not allowed.")
        output.extend(original_lines[cursor:old_index])

        current = old_index
        for line in hunk[1:]:
            if not line or line.startswith("\\"):
                continue
            marker = line[0]
            content = line[1:]
            if marker == " ":
                if current >= len(original_lines) or original_lines[current] != content:
                    raise ValueError("Diff context does not match current file content.")
                output.append(content)
                current += 1
            elif marker == "-":
                if current >= len(original_lines) or original_lines[current] != content:
                    raise ValueError("Diff removal does not match current file content.")
                current += 1
            elif marker == "+":
                output.append(content)
        cursor = current
    output.extend(original_lines[cursor:])
    return output


def _strip_diff_prefix(path_text: str) -> str:
    if path_text.startswith("a/") or path_text.startswith("b/"):
        return path_text[2:]
    return path_text


def _appears_binary(path: Path) -> bool:
    try:
        return b"\0" in path.read_bytes()[:8192]
    except OSError:
        return True


def _detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
