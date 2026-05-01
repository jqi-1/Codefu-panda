"""Advisory AI connector and proposal parsing."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .model_protocol import ModelProtocolError, parse_model_proposal
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
                        "requested structured JSON object. Do not execute commands, "
                        "do not request shell metacharacters, and do not propose "
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
            'Return JSON exactly as {"type":"command","command":"single-line command"}. '
            "The command must be one line, relevant to the user task, and contain "
            "no shell metacharacters."
        )
    elif request_type == "edit":
        if target_file_path is None or target_file_contents is None:
            raise ValueError("Edit prompts require a target file path and contents")
        expected = (
            'Return JSON exactly as {"type":"edit","diff":"unified diff"}. '
            "The diff must affect only the target file and be based on the "
            "current target file contents below."
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
        (
            "- Do not propose destructive actions, shell metacharacters, pipes, "
            "redirects, or command chaining."
        ),
        "- Keep paths project-relative and inside the project root.",
        "- For Python package operations, use python -m pip instead of direct pip.",
    ]

    if request_type == "run":
        lines.extend(
            [
                (
                    "- Prefer read-only or test commands unless the user explicitly "
                    "asks for dependency or build changes."
                ),
            ]
        )
    if request_type == "edit":
        lines.extend(
            [
                "- The unified diff must use --- a/<target> and +++ b/<target> paths.",
                "- Do not delete files, rename files, or edit multiple files.",
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
    try:
        data = json.loads(raw_output.strip())
    except json.JSONDecodeError as exc:
        raise AIConnectorError("Suggestion output was not JSON") from exc
    if not isinstance(data, dict) or set(data.keys()) != {"suggestions"}:
        raise AIConnectorError("Suggestion output did not match the required schema")
    suggestions_data = data["suggestions"]
    if not isinstance(suggestions_data, list):
        raise AIConnectorError("Suggestion output did not contain a suggestion list")
    suggestions: list[str] = []
    for item in suggestions_data:
        if not isinstance(item, str):
            raise AIConnectorError("Suggestion output contained a non-string suggestion")
        suggestion = item.strip()
        if suggestion:
            suggestions.append(suggestion)
    if len(suggestions) != 2:
        raise AIConnectorError("Suggestion output did not contain exactly two items")
    return SuggestionProposal(suggestions=suggestions)


def parse_command(raw_output: str) -> CommandProposal:
    return parse_commands(raw_output)[0]


def parse_commands(raw_output: str) -> list[CommandProposal]:
    try:
        proposal = parse_model_proposal(raw_output)
    except ModelProtocolError as exc:
        raise AIConnectorError("Command output did not match the strict model protocol") from exc
    if not isinstance(proposal, CommandProposal):
        raise AIConnectorError("Command output was not a command proposal")
    return [proposal]


def parse_edit(raw_output: str) -> EditProposal:
    return parse_edits(raw_output)[0]


def parse_edits(raw_output: str) -> list[EditProposal]:
    try:
        proposal = parse_model_proposal(raw_output)
    except ModelProtocolError as exc:
        raise AIConnectorError("Edit output did not match the strict model protocol") from exc
    if not isinstance(proposal, EditProposal):
        raise AIConnectorError("Edit output was not an edit proposal")
    diff = proposal.diff
    if not diff.startswith("--- ") or "\n+++ " not in diff or "\n@@ " not in diff:
        raise AIConnectorError("Edit output contained a non-unified diff")
    return [proposal]


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
