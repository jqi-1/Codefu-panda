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


class AIConnectorError(RuntimeError):
    """Raised when the advisory backend cannot produce structured output."""


@dataclass(frozen=True)
class AIConnector:
    endpoint: str = DEFAULT_LM_STUDIO_ENDPOINT
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 10

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an advisory local coding assistant. Return only the "
                        "requested structured proposal. Do not execute commands, do "
                        "not request shell metacharacters, and do not propose "
                        "destructive actions."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
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


def build_prompt(request_type: str, summary: ProjectSummary) -> str:
    if request_type == "suggest":
        expected = (
            'Return JSON exactly as {"suggestions":["Code quality: ...", '
            '"Project health: ..."]}. Include exactly two suggestions.'
        )
    elif request_type == "run":
        expected = (
            'Return JSON exactly as {"command":"single-line command"}. The command '
            "must contain no shell metacharacters and must be relevant to the project."
        )
    elif request_type == "edit":
        expected = (
            'Return JSON exactly as {"diff":"unified diff"}. Include exactly one '
            "unified diff for one focused edit."
        )
    else:
        raise ValueError(f"Unknown request type: {request_type}")

    return "\n".join(
        [
            f"Request type: {request_type}",
            "",
            "Project summary:",
            summary.to_prompt_context(),
            "",
            expected,
        ]
    )


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
    command = raw_output.strip()
    try:
        data = json.loads(command)
        if isinstance(data, dict) and isinstance(data.get("command"), str):
            command = data["command"].strip()
    except json.JSONDecodeError:
        pass
    if not command or "\n" in command or "\r" in command:
        raise AIConnectorError("Command output was not exactly one line")
    return CommandProposal(command=command)


def parse_edit(raw_output: str) -> EditProposal:
    diff = raw_output.strip()
    try:
        data = json.loads(diff)
        if isinstance(data, dict) and isinstance(data.get("diff"), str):
            diff = data["diff"].strip()
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:diff)?\s*(.*?)```", diff, re.DOTALL)
        if fenced:
            diff = fenced.group(1).strip()

    if not diff.startswith("--- ") or "\n+++ " not in diff or "\n@@ " not in diff:
        raise AIConnectorError("Edit output was not a unified diff")
    return EditProposal(diff=diff)


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
