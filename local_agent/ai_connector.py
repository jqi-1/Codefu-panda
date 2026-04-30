"""Advisory AI connector and proposal parsing."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .models import CommandProposal, EditProposal, ProjectSummary, SuggestionProposal


DEFAULT_LM_STUDIO_ENDPOINT = "http://localhost:1234/v1/chat/completions"
DEFAULT_MODEL = "local-model"
MAX_PROPOSALS = 3


class AIConnectorError(RuntimeError):
    """Raised when the advisory backend cannot produce structured output."""


@dataclass(frozen=True)
class AIConnector:
    endpoint: str = DEFAULT_LM_STUDIO_ENDPOINT
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 10

    def suggest(
        self,
        summary: ProjectSummary,
        user_task: str = "",
    ) -> SuggestionProposal:
        prompt = build_prompt("suggest", summary, user_task=user_task)
        return parse_suggestions(self.generate(prompt))

    def propose_command(
        self,
        summary: ProjectSummary,
        user_task: str,
    ) -> CommandProposal:
        return self.propose_commands(summary, user_task)[0]

    def propose_commands(
        self,
        summary: ProjectSummary,
        user_task: str,
    ) -> list[CommandProposal]:
        prompt = build_prompt("run", summary, user_task=user_task)
        return parse_commands(self.generate(prompt))

    def propose_edit(
        self,
        summary: ProjectSummary,
        user_task: str,
        target_file_path: str,
        target_file_contents: str,
    ) -> EditProposal:
        return self.propose_edits(
            summary,
            user_task,
            target_file_path,
            target_file_contents,
        )[0]

    def propose_edits(
        self,
        summary: ProjectSummary,
        user_task: str,
        target_file_path: str,
        target_file_contents: str,
    ) -> list[EditProposal]:
        prompt = build_prompt(
            "edit",
            summary,
            user_task=user_task,
            target_file_path=target_file_path,
            target_file_contents=target_file_contents,
        )
        return parse_edits(self.generate(prompt))

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an advisory local coding assistant. Return only the "
                        "requested structured proposal or proposals. Do not execute commands, do "
                        "not request shell metacharacters, and do not propose "
                        "destructive actions."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except (OSError, urllib.error.URLError) as exc:
            raise AIConnectorError(f"AI backend unavailable: {exc}") from exc

        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AIConnectorError("AI backend returned an invalid response") from exc


def build_prompt(
    request_type: str,
    summary: ProjectSummary,
    user_task: str = "",
    target_file_path: str | None = None,
    target_file_contents: str | None = None,
) -> str:
    task_text = user_task.strip() or "No specific user task was provided."

    if request_type == "suggest":
        expected = (
            'Return JSON exactly as {"suggestions":["Code quality: ...",'
            '"Project health: ..."]}. Include exactly two suggestions tailored '
            "to the user task and project summary."
        )
    elif request_type == "run":
        expected = (
            'Return JSON exactly as {"commands":["single-line command"]}. Include '
            f"one to {MAX_PROPOSALS} command strings. Each command must be one "
            "line, relevant to the user task, and contain no shell metacharacters."
        )
    elif request_type == "edit":
        if target_file_path is None or target_file_contents is None:
            raise ValueError("Edit prompts require a target file path and contents")
        expected = (
            'Return JSON exactly as {"diffs":["unified diff"]}. Include one to '
            f"{MAX_PROPOSALS} unified diffs. Each diff must affect only the target "
            "file and be based on the current target file contents below."
        )
    else:
        raise ValueError(f"Unknown request type: {request_type}")

    lines = [
        f"Request type: {request_type}",
        "",
        "Project summary:",
        summary.to_prompt_context(),
        "",
        "User task:",
        task_text,
        "",
        "Safety constraints:",
        "- Return only valid JSON matching the schema. No markdown fences or commentary.",
        "- Do not execute commands or claim that commands have been executed.",
        "- Do not propose destructive actions, shell metacharacters, pipes, redirects, or command chaining.",
        "- Keep paths project-relative and inside the project root.",
        "- For Python package operations, use python -m pip instead of direct pip.",
    ]

    if request_type == "run":
        lines.extend(
            [
                "- Prefer read-only or test commands unless the user explicitly asks for dependency or build changes.",
                f"- Include no more than {MAX_PROPOSALS} command alternatives.",
            ]
        )
    if request_type == "edit":
        lines.extend(
            [
                "- The unified diff must use --- a/<target> and +++ b/<target> paths.",
                "- Do not delete files, rename files, or edit multiple files.",
                f"- Include no more than {MAX_PROPOSALS} edit alternatives.",
                "",
                f"Target file path: {target_file_path}",
                "Current target file contents:",
                "----- BEGIN FILE -----",
                target_file_contents if target_file_contents is not None else "",
                "----- END FILE -----",
            ]
        )

    lines.extend(
        [
            "",
            "Output schema:",
            expected,
        ]
    )
    return "\n".join(lines)


def parse_suggestions(raw_output: str) -> SuggestionProposal:
    stripped = raw_output.strip()
    suggestions: list[str] | None = None
    try:
        data = json.loads(stripped)
        if isinstance(data, dict) and isinstance(data.get("suggestions"), list):
            suggestions = [str(item).strip() for item in data["suggestions"]]
    except json.JSONDecodeError:
        suggestions = _parse_numbered_suggestions(stripped)

    if suggestions is None:
        suggestions = _parse_numbered_suggestions(stripped)
    suggestions = [item for item in suggestions if item]
    if len(suggestions) != 2:
        raise AIConnectorError("Suggestion output did not contain exactly two items")
    return SuggestionProposal(suggestions=suggestions)


def parse_command(raw_output: str) -> CommandProposal:
    return parse_commands(raw_output)[0]


def parse_commands(raw_output: str) -> list[CommandProposal]:
    try:
        data = json.loads(raw_output.strip())
    except json.JSONDecodeError as exc:
        raise AIConnectorError("Command output was not JSON") from exc
    if not isinstance(data, dict):
        raise AIConnectorError("Command output did not match the required schema")
    if set(data.keys()) == {"command"}:
        commands = [data["command"]]
    elif set(data.keys()) == {"commands"}:
        commands = data["commands"]
    else:
        raise AIConnectorError("Command output did not match the required schema")
    if not isinstance(commands, list):
        raise AIConnectorError("Command output did not contain a command list")
    proposals: list[CommandProposal] = []
    for item in commands:
        if not isinstance(item, str):
            raise AIConnectorError("Command output contained a non-string command")
        command = item.strip()
        if not command or "\n" in command or "\r" in command:
            raise AIConnectorError("Command output contained a non-single-line command")
        proposals.append(CommandProposal(command=command))
    if not 1 <= len(proposals) <= MAX_PROPOSALS:
        raise AIConnectorError(
            f"Command output must contain between 1 and {MAX_PROPOSALS} commands"
        )
    return proposals


def parse_edit(raw_output: str) -> EditProposal:
    return parse_edits(raw_output)[0]


def parse_edits(raw_output: str) -> list[EditProposal]:
    try:
        data = json.loads(raw_output.strip())
    except json.JSONDecodeError as exc:
        raise AIConnectorError("Edit output was not JSON") from exc
    if not isinstance(data, dict):
        raise AIConnectorError("Edit output did not match the required schema")
    if set(data.keys()) == {"diff"}:
        diffs = [data["diff"]]
    elif set(data.keys()) == {"diffs"}:
        diffs = data["diffs"]
    else:
        raise AIConnectorError("Edit output did not match the required schema")
    if not isinstance(diffs, list):
        raise AIConnectorError("Edit output did not contain a diff list")
    proposals: list[EditProposal] = []
    for item in diffs:
        if not isinstance(item, str):
            raise AIConnectorError("Edit output contained a non-string diff")
        diff = item.strip()
        if not diff.startswith("--- ") or "\n+++ " not in diff or "\n@@ " not in diff:
            raise AIConnectorError("Edit output contained a non-unified diff")
        proposals.append(EditProposal(diff=diff))
    if not 1 <= len(proposals) <= MAX_PROPOSALS:
        raise AIConnectorError(
            f"Edit output must contain between 1 and {MAX_PROPOSALS} diffs"
        )
    return proposals


def deterministic_suggestions(summary: ProjectSummary) -> SuggestionProposal:
    if summary.primary_language == "Python":
        code_quality = (
            "Code quality: Add type hints to public Python functions as they are "
            "introduced."
        )
    elif summary.primary_language == "Unknown":
        code_quality = (
            "Code quality: Add a small, readable entry point once the first source "
            "files are created."
        )
    else:
        code_quality = (
            f"Code quality: Keep the {summary.primary_language} entry points small "
            "and split larger logic into focused helpers."
        )

    if summary.tests_detected:
        project_health = "Project health: Document the existing test command in a README."
    else:
        project_health = "Project health: Add a minimal test file or smoke test."
    return SuggestionProposal([code_quality, project_health])


def _parse_numbered_suggestions(text: str) -> list[str]:
    suggestions: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip()
        match = re.match(r"^\d+[.)]\s+(.*)$", cleaned)
        if match:
            suggestions.append(match.group(1).strip())
    return suggestions
