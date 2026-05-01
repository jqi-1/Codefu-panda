"""Strict JSON protocol for untrusted model proposals."""

from __future__ import annotations

import json
from typing import Union

from .models import CommandProposal, EditProposal, PlanProposal, SummaryProposal


class ModelProtocolError(ValueError):
    """Raised when model output does not exactly match the local protocol."""


ModelProposal = Union[CommandProposal, EditProposal, PlanProposal, SummaryProposal]


def parse_model_proposal(text: str) -> ModelProposal:
    """Parse exactly one JSON object returned by the advisory model."""

    stripped = text.strip()
    if stripped.startswith("```"):
        raise ModelProtocolError("Model output must not use Markdown code fences")

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ModelProtocolError("Model output must be exactly one JSON object") from exc

    if not isinstance(data, dict):
        raise ModelProtocolError("Model output must be a JSON object")
    if "type" not in data:
        raise ModelProtocolError("Model output is missing required field `type`")

    proposal_type = data["type"]
    if proposal_type == "command":
        _require_exact_keys(data, {"type", "command"})
        return CommandProposal(command=_required_non_empty_string(data, "command"))
    if proposal_type == "edit":
        _require_exact_keys(data, {"type", "diff"})
        return EditProposal(diff=_required_non_empty_string(data, "diff"))
    if proposal_type == "plan":
        _require_exact_keys(data, {"type", "steps"})
        steps = data["steps"]
        if not isinstance(steps, list):
            raise ModelProtocolError("Plan proposal `steps` must be a list")
        parsed_steps: list[str] = []
        for step in steps:
            if not isinstance(step, str) or not step.strip():
                raise ModelProtocolError("Plan proposal steps must be non-empty strings")
            parsed_steps.append(step.strip())
        return PlanProposal(steps=parsed_steps)
    if proposal_type == "summary":
        _require_exact_keys(data, {"type", "summary"})
        return SummaryProposal(summary=_required_non_empty_string(data, "summary"))

    raise ModelProtocolError(f"Unknown model proposal type: {proposal_type!r}")


def _require_exact_keys(data: dict[str, object], expected: set[str]) -> None:
    actual = set(data.keys())
    if actual != expected:
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(f"`{key}`" for key in missing))
        if extra:
            details.append("extra " + ", ".join(f"`{key}`" for key in extra))
        raise ModelProtocolError("Model output schema mismatch: " + "; ".join(details))


def _required_non_empty_string(data: dict[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ModelProtocolError(f"Model output field `{key}` must be a string")
    stripped = value.strip()
    if not stripped:
        raise ModelProtocolError(f"Model output field `{key}` must be non-empty")
    if key == "command" and ("\n" in stripped or "\r" in stripped):
        raise ModelProtocolError("Command proposal must be a single line")
    return stripped
