"""CLI entry point for the local safety-first coding agent."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ai_connector import (
    AIConnector,
    AIConnectorError,
    build_prompt,
    deterministic_suggestions,
    parse_command,
    parse_edit,
    parse_suggestions,
)
from .command_runner import DEFAULT_TIMEOUT_SECONDS, CommandRunner
from .file_watcher import DEFAULT_IGNORED_DIRECTORIES, ProjectScanner
from .logger import AgentLogger, LoggerError
from .models import CommandProposal, EditProposal, ProjectSummary, SuggestionProposal
from .permission_manager import DEFAULT_ALLOWED_COMMANDS, PermissionManager


@dataclass(frozen=True)
class AgentConfig:
    allowed_commands: set[str]
    ignored_directories: set[str]
    ai_endpoint: str
    ai_model: str
    command_timeout: int


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local safety-first coding agent CLI")
    parser.add_argument("project_root", help="Project root for the agent to inspect")
    args = parser.parse_args(argv)

    try:
        project_root = Path(args.project_root).resolve(strict=True)
    except OSError as exc:
        print(f"Error: could not resolve project root: {exc}")
        return 2
    if not project_root.is_dir():
        print(f"Error: project root is not a directory: {project_root}")
        return 2

    try:
        logger = AgentLogger(project_root)
    except LoggerError as exc:
        print(f"Error: logging is not safe: {exc}")
        return 2

    logger.log("STARTUP", "Agent started", project_root=project_root)
    config = load_config(project_root, logger)
    scanner = ProjectScanner(project_root, config.ignored_directories, logger)
    summary = scanner.scan()
    print(summary.to_display_text())
    logger.log("PROJECT_SUMMARY", "Scanned project", summary=summary.to_display_text())

    connector = AIConnector(config.ai_endpoint, config.ai_model)
    permission_manager = PermissionManager(project_root, logger, config.allowed_commands)
    runner = CommandRunner(project_root, config.command_timeout)

    suggestions = get_suggestions(connector, summary, logger)
    display_suggestions(suggestions)
    logger.log("SUGGESTIONS", "Displayed startup suggestions", suggestions=suggestions.suggestions)

    try:
        run_menu_loop(
            connector,
            summary,
            permission_manager,
            runner,
            logger,
            input_func=input,
            output_func=print,
        )
    except KeyboardInterrupt:
        print("\nExiting.")
        logger.log("SHUTDOWN", "Agent interrupted by user")
        return 130
    return 0


def load_config(project_root: Path, logger: AgentLogger) -> AgentConfig:
    from .ai_connector import DEFAULT_LM_STUDIO_ENDPOINT, DEFAULT_MODEL

    config = AgentConfig(
        allowed_commands=set(DEFAULT_ALLOWED_COMMANDS),
        ignored_directories=set(DEFAULT_IGNORED_DIRECTORIES),
        ai_endpoint=DEFAULT_LM_STUDIO_ENDPOINT,
        ai_model=DEFAULT_MODEL,
        command_timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    config_path = project_root / ".agent_config.json"
    if not config_path.exists():
        return config

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("configuration root must be an object")
        allowed_commands = set(config.allowed_commands)
        ignored_directories = set(config.ignored_directories)

        additional_allowed = data.get("additional_allowed_commands", [])
        if not isinstance(additional_allowed, list) or not all(
            isinstance(item, str) and item for item in additional_allowed
        ):
            raise ValueError("additional_allowed_commands must be a list of strings")
        allowed_commands.update(additional_allowed)

        additional_ignored = data.get("additional_ignored_directories", [])
        if not isinstance(additional_ignored, list) or not all(
            isinstance(item, str) and item for item in additional_ignored
        ):
            raise ValueError("additional_ignored_directories must be a list of strings")
        ignored_directories.update(additional_ignored)

        endpoint = data.get("ai_endpoint", config.ai_endpoint)
        model = data.get("ai_model", config.ai_model)
        timeout = data.get("command_timeout", config.command_timeout)
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("ai_endpoint must be a non-empty string")
        if not isinstance(model, str) or not model:
            raise ValueError("ai_model must be a non-empty string")
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("command_timeout must be a positive integer")

        return AgentConfig(
            allowed_commands=allowed_commands,
            ignored_directories=ignored_directories,
            ai_endpoint=endpoint,
            ai_model=model,
            command_timeout=timeout,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Invalid .agent_config.json; using defaults: {exc}")
        logger.log("ERROR", "Invalid configuration; using defaults", error_message=exc)
        return config


def run_menu_loop(
    connector: AIConnector,
    summary: ProjectSummary,
    permission_manager: PermissionManager,
    runner: CommandRunner,
    logger: AgentLogger,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> None:
    while True:
        raw_choice = input_func("Type suggest, run, edit, or quit: ")
        # Menu commands are normalized consistently so `QUIT` behaves like `quit`.
        choice = raw_choice.strip().lower()
        logger.log("MENU_INPUT", "User entered menu input", input=raw_choice.strip())

        if choice == "suggest":
            suggestions = get_suggestions(connector, summary, logger)
            display_suggestions(suggestions, output_func)
            logger.log("SUGGESTIONS", "Displayed menu suggestions", suggestions=suggestions.suggestions)
        elif choice == "run":
            handle_run(connector, summary, permission_manager, runner, logger, input_func, output_func)
        elif choice == "edit":
            handle_edit(connector, summary, permission_manager, logger, input_func, output_func)
        elif choice == "quit":
            logger.log("SHUTDOWN", "Agent exited cleanly")
            return
        else:
            output_func("Invalid option. Type suggest, run, edit, or quit.")
            logger.log("INVALID_MENU_INPUT", "Invalid menu input", input=raw_choice.strip())


def get_suggestions(
    connector: AIConnector,
    summary: ProjectSummary,
    logger: AgentLogger,
) -> SuggestionProposal:
    prompt = build_prompt("suggest", summary)
    try:
        raw = connector.generate(prompt)
        return parse_suggestions(raw)
    except AIConnectorError as exc:
        logger.log("ERROR", "Using deterministic suggestion fallback", error_message=exc)
        return deterministic_suggestions(summary)


def handle_run(
    connector: AIConnector,
    summary: ProjectSummary,
    permission_manager: PermissionManager,
    runner: CommandRunner,
    logger: AgentLogger,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> None:
    proposal = _get_command_proposal(connector, summary, logger, output_func)
    if proposal is None:
        return

    output_func("Proposed command:")
    output_func(proposal.command)
    logger.log("PROPOSE_COMMAND", "Proposed command", command=proposal.command)

    validation = permission_manager.validate_command(proposal.command)
    if not validation.ok:
        output_func(validation.message)
        logger.log(validation.event_type, validation.message, command=proposal.command)
        return

    prompt = "Run this command? (yes/no)"
    if validation.risky:
        prompt = (
            "Warning: this command may modify dependencies, generated files, or the "
            "project environment.\nRun this command? (yes/no)"
        )
    approved = permission_manager.confirm(prompt, input_func, output_func)
    if not approved:
        logger.log("COMMAND_DENIED", "User denied command", command=proposal.command, user_decision="no")
        return

    logger.log("COMMAND_APPROVED", "User approved command", command=proposal.command, user_decision="yes")
    validation = permission_manager.validate_command(proposal.command)
    if not validation.ok:
        output_func(validation.message)
        logger.log(validation.event_type, validation.message, command=proposal.command)
        return

    result = runner.run(proposal.command, validation.tokens)
    output_func("stdout")
    output_func(result.stdout)
    output_func("stderr")
    output_func(result.stderr)
    if result.timed_out:
        output_func(result.error_message)
        logger.log(
            "COMMAND_TIMEOUT",
            result.error_message,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )
    else:
        if result.error_message:
            output_func(result.error_message)
        logger.log(
            "COMMAND_RESULT",
            "Command finished",
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            error_message=result.error_message,
        )


def handle_edit(
    connector: AIConnector,
    summary: ProjectSummary,
    permission_manager: PermissionManager,
    logger: AgentLogger,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> None:
    proposal = _get_edit_proposal(connector, summary, logger, output_func)
    if proposal is None:
        return
    output_func("Proposed edit:")
    output_func(proposal.diff)
    logger.log("PROPOSE_EDIT", "Proposed edit", diff=proposal.diff)

    approved = permission_manager.confirm("Apply this edit? (yes/no)", input_func, output_func)
    if not approved:
        logger.log("EDIT_DENIED", "User denied edit", diff=proposal.diff, user_decision="no")
        return

    logger.log("EDIT_APPROVED", "User approved edit", diff=proposal.diff, user_decision="yes")
    result = permission_manager.apply_unified_diff(proposal.diff)
    output_func(result.message)


def _get_command_proposal(
    connector: AIConnector,
    summary: ProjectSummary,
    logger: AgentLogger,
    output_func: Callable[[str], None],
) -> CommandProposal | None:
    prompt = build_prompt("run", summary)
    try:
        raw = connector.generate(prompt)
        return parse_command(raw)
    except AIConnectorError as exc:
        output_func("Could not generate a structured command proposal.")
        logger.log("ERROR", "Command proposal failed closed", error_message=exc)
        return None


def _get_edit_proposal(
    connector: AIConnector,
    summary: ProjectSummary,
    logger: AgentLogger,
    output_func: Callable[[str], None],
) -> EditProposal | None:
    prompt = build_prompt("edit", summary)
    try:
        raw = connector.generate(prompt)
        return parse_edit(raw)
    except AIConnectorError as exc:
        output_func("Could not generate a structured edit proposal.")
        logger.log("ERROR", "Edit proposal failed closed", error_message=exc)
        return None


def display_suggestions(
    proposal: SuggestionProposal,
    output_func: Callable[[str], None] = print,
) -> None:
    output_func("")
    output_func("Suggestions")
    output_func("")
    for index, suggestion in enumerate(proposal.suggestions, start=1):
        output_func(f"{index}. {suggestion}")


if __name__ == "__main__":
    sys.exit(main())
