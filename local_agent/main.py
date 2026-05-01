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
    parse_commands,
    parse_edits,
    parse_suggestions,
)
from .command_runner import DEFAULT_TIMEOUT_SECONDS, CommandRunner
from .file_watcher import (
    DEFAULT_IGNORED_DIRECTORIES,
    FileTooLargeError,
    ProjectScanner,
    SandboxError,
)
from .logger import AgentLogger, LoggerError
from .models import CommandProposal, EditProposal, ProjectSummary, SuggestionProposal
from .permission_manager import (
    DEFAULT_ALLOWED_COMMANDS,
    PermissionManager,
    validate_allowed_commands,
)
from .repo_summary import summarize_repo
from .snapshots import SnapshotError, restore_snapshot


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
    parser.add_argument(
        "action",
        nargs="?",
        choices=("restore", "summarize"),
        help="Optional non-interactive action to run",
    )
    parser.add_argument(
        "snapshot_id",
        nargs="?",
        help="Snapshot id to restore; defaults to the most recent snapshot",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate proposed commands and edits without executing or applying them",
    )
    args = parser.parse_args(argv)

    try:
        project_root = Path(args.project_root).resolve(strict=True)
    except OSError as exc:
        print(f"Error: could not resolve project root: {exc}")
        return 2
    if not project_root.is_dir():
        print(f"Error: project root is not a directory: {project_root}")
        return 2

    if args.action == "summarize":
        if args.snapshot_id:
            print("Error: summarize does not accept a snapshot id.")
            return 2
        print(summarize_repo(project_root).to_display_text())
        return 0

    if args.action == "restore":
        try:
            result = restore_snapshot(project_root, args.snapshot_id)
        except SnapshotError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Restored snapshot: {result.snapshot.id}")
        for path in result.restored_paths:
            print(f"Restored: {path.as_posix()}")
        for path in result.removed_paths:
            print(f"Removed: {path.as_posix()}")
        return 0

    if args.snapshot_id:
        print("Error: snapshot id is only valid with the restore action.")
        return 2

    try:
        logger = AgentLogger(project_root)
    except LoggerError as exc:
        print(f"Error: logging is not safe: {exc}")
        return 2

    logger.log("STARTUP", "Agent started", project_root=project_root)
    if args.dry_run:
        print("Dry-run mode: commands and edits will be validated but not executed.")
        logger.log("DRY_RUN", "Agent started in dry-run mode")
    config = load_config(project_root, logger)
    scanner = ProjectScanner(project_root, config.ignored_directories, logger)
    summary = scanner.scan()
    print(summary.to_display_text())
    logger.log("PROJECT_SUMMARY", "Scanned project", summary=summary.to_display_text())

    connector = AIConnector(config.ai_endpoint, config.ai_model)
    permission_manager = PermissionManager(project_root, logger, config.allowed_commands)
    runner = CommandRunner(project_root, config.command_timeout)

    startup_task = input("What kind of suggestions do you want? ").strip()
    logger.log(
        "USER_TASK",
        "Captured startup suggestion task",
        request_type="suggest",
        user_task=startup_task,
    )
    suggestions = get_suggestions(connector, summary, logger, startup_task)
    display_suggestions(suggestions)
    logger.log("SUGGESTIONS", "Displayed startup suggestions", suggestions=suggestions.suggestions)

    try:
        run_menu_loop(
            connector,
            summary,
            permission_manager,
            runner,
            logger,
            scanner=scanner,
            input_func=input,
            output_func=print,
            dry_run=args.dry_run,
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
        allowed_commands.update(validate_allowed_commands(set(additional_allowed)))

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
    scanner: ProjectScanner | None = None,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
    dry_run: bool = False,
) -> None:
    if scanner is None:
        scanner = ProjectScanner(summary.root, logger=logger)

    while True:
        raw_choice = input_func("Type suggest, run, edit, or quit: ")
        # Menu commands are normalized consistently so `QUIT` behaves like `quit`.
        choice = raw_choice.strip().lower()
        logger.log("MENU_INPUT", "User entered menu input", input=raw_choice.strip())

        if choice == "suggest":
            user_task = input_func("What kind of suggestions do you want? ").strip()
            logger.log(
                "USER_TASK",
                "Captured suggestion task",
                request_type="suggest",
                user_task=user_task,
            )
            suggestions = get_suggestions(connector, summary, logger, user_task)
            display_suggestions(suggestions, output_func)
            logger.log(
                "SUGGESTIONS",
                "Displayed menu suggestions",
                suggestions=suggestions.suggestions,
            )
        elif choice == "run":
            handle_run(
                connector,
                summary,
                permission_manager,
                runner,
                logger,
                input_func,
                output_func,
                dry_run=dry_run,
            )
        elif choice == "edit":
            handle_edit(
                connector,
                summary,
                scanner,
                permission_manager,
                logger,
                input_func,
                output_func,
                dry_run=dry_run,
            )
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
    user_task: str = "",
) -> SuggestionProposal:
    try:
        return _connector_suggestions(connector, summary, user_task)
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
    dry_run: bool = False,
) -> None:
    user_task = input_func("What command/task do you want help with? ").strip()
    logger.log("USER_TASK", "Captured command task", request_type="run", user_task=user_task)
    if not user_task:
        output_func("No command task entered; cancelled.")
        return

    proposals = _get_command_proposals(connector, summary, user_task, logger, output_func)
    if not proposals:
        return
    proposal = _select_command_proposal(proposals, logger, input_func, output_func)
    if proposal is None:
        return

    output_func("Selected command:")
    output_func(proposal.command)
    logger.log("SELECT_COMMAND", "Selected command proposal", command=proposal.command)

    validation = permission_manager.validate_command(proposal.command)
    if not validation.ok:
        output_func(validation.message)
        logger.log(validation.event_type, validation.message, command=proposal.command)
        return

    if dry_run:
        if validation.risky:
            output_func("Dry-run: command is risky and would require confirmation.")
            output_func(validation.risk_message)
        else:
            output_func("Dry-run: command would be allowed.")
        logger.log(
            "COMMAND_DRY_RUN",
            "Dry-run command validation completed",
            command=proposal.command,
            risky=validation.risky,
            risk_message=validation.risk_message,
        )
        return

    prompt = "Run this command? (yes/no)"
    if validation.risk_message:
        prompt = f"{validation.risk_message}\n{prompt}"
    approved = permission_manager.confirm(prompt, input_func, output_func)
    if not approved:
        logger.log(
            "COMMAND_DENIED",
            "User denied command",
            command=proposal.command,
            user_decision="no",
        )
        return

    logger.log(
        "COMMAND_APPROVED",
        "User approved command",
        command=proposal.command,
        user_decision="yes",
    )
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
    scanner: ProjectScanner,
    permission_manager: PermissionManager,
    logger: AgentLogger,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    dry_run: bool = False,
) -> None:
    user_task = input_func("What code change do you want to make? ").strip()
    logger.log("USER_TASK", "Captured edit task", request_type="edit", user_task=user_task)
    if not user_task:
        output_func("No code change entered; cancelled.")
        return

    raw_file_path = input_func("Which file should be edited? ").strip()
    if not raw_file_path:
        output_func("No target file entered; cancelled.")
        return
    try:
        target_file_path = _resolve_existing_project_file(scanner.project_root, raw_file_path)
        file_contents = scanner.read_text_file(target_file_path)
    except ValueError as exc:
        output_func(str(exc))
        logger.log("SANDBOX_DENIED", "Edit target rejected", path=raw_file_path, error_message=exc)
        return
    except SandboxError as exc:
        output_func(str(exc))
        logger.log("SANDBOX_DENIED", "Edit target rejected", path=raw_file_path, error_message=exc)
        return
    except FileTooLargeError as exc:
        output_func(f"Cannot read target file: {exc}")
        logger.log("EDIT_FAILED", "Edit target too large", path=raw_file_path, error_message=exc)
        return
    except UnicodeDecodeError as exc:
        output_func("Cannot read target file as UTF-8 text; edit aborted.")
        logger.log("EDIT_FAILED", "Edit target is not text", path=raw_file_path, error_message=exc)
        return
    except OSError as exc:
        output_func(f"Cannot read target file: {exc}")
        logger.log(
            "EDIT_FAILED",
            "Could not read edit target",
            path=raw_file_path,
            error_message=exc,
        )
        return

    logger.log("EDIT_TARGET", "Loaded edit target", path=target_file_path)
    proposals = _get_edit_proposals(
        connector,
        summary,
        user_task,
        target_file_path,
        file_contents,
        logger,
        output_func,
    )
    if not proposals:
        return
    proposal = _select_edit_proposal(proposals, logger, input_func, output_func)
    if proposal is None:
        return

    logger.log("SELECT_EDIT", "Selected edit proposal", diff=proposal.diff)

    if dry_run:
        result = permission_manager.apply_unified_diff(proposal.diff, dry_run=True)
        output_func(result.message)
        return

    approved = permission_manager.confirm("Apply this edit? (yes/no)", input_func, output_func)
    if not approved:
        logger.log("EDIT_DENIED", "User denied edit", diff=proposal.diff, user_decision="no")
        return

    logger.log("EDIT_APPROVED", "User approved edit", diff=proposal.diff, user_decision="yes")
    result = permission_manager.apply_unified_diff(proposal.diff)
    output_func(result.message)
    if result.ok and result.snapshot_id:
        output_func(f"Snapshot saved: {result.snapshot_id}")


def _get_command_proposals(
    connector: AIConnector,
    summary: ProjectSummary,
    user_task: str,
    logger: AgentLogger,
    output_func: Callable[[str], None],
) -> list[CommandProposal]:
    try:
        return _connector_command(connector, summary, user_task)
    except AIConnectorError as exc:
        output_func("Could not generate structured command proposals.")
        logger.log("ERROR", "Command proposal failed closed", error_message=exc)
        return []


def _get_edit_proposals(
    connector: AIConnector,
    summary: ProjectSummary,
    user_task: str,
    target_file_path: str,
    target_file_contents: str,
    logger: AgentLogger,
    output_func: Callable[[str], None],
) -> list[EditProposal]:
    try:
        return _connector_edit(
            connector,
            summary,
            user_task,
            target_file_path,
            target_file_contents,
        )
    except AIConnectorError as exc:
        output_func("Could not generate structured edit proposals.")
        logger.log("ERROR", "Edit proposal failed closed", error_message=exc)
        return []


def _connector_suggestions(
    connector: AIConnector,
    summary: ProjectSummary,
    user_task: str,
) -> SuggestionProposal:
    suggest = getattr(connector, "suggest", None)
    if callable(suggest):
        return suggest(summary, user_task)
    raw = connector.generate(build_prompt("suggest", summary, user_task=user_task))
    return parse_suggestions(raw)


def _connector_command(
    connector: AIConnector,
    summary: ProjectSummary,
    user_task: str,
) -> list[CommandProposal]:
    propose_commands = getattr(connector, "propose_commands", None)
    if callable(propose_commands):
        return propose_commands(summary, user_task)
    propose_command = getattr(connector, "propose_command", None)
    if callable(propose_command):
        return [propose_command(summary, user_task)]
    raw = connector.generate(build_prompt("run", summary, user_task=user_task))
    return parse_commands(raw)


def _connector_edit(
    connector: AIConnector,
    summary: ProjectSummary,
    user_task: str,
    target_file_path: str,
    target_file_contents: str,
) -> list[EditProposal]:
    propose_edits = getattr(connector, "propose_edits", None)
    if callable(propose_edits):
        return propose_edits(summary, user_task, target_file_path, target_file_contents)
    propose_edit = getattr(connector, "propose_edit", None)
    if callable(propose_edit):
        return [propose_edit(summary, user_task, target_file_path, target_file_contents)]
    raw = connector.generate(
        build_prompt(
            "edit",
            summary,
            user_task=user_task,
            target_file_path=target_file_path,
            target_file_contents=target_file_contents,
        )
    )
    return parse_edits(raw)


def _select_command_proposal(
    proposals: list[CommandProposal],
    logger: AgentLogger,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> CommandProposal | None:
    if len(proposals) == 1:
        output_func("Proposed command:")
        output_func(proposals[0].command)
        logger.log("PROPOSE_COMMAND", "Proposed command", command=proposals[0].command)
        return proposals[0]

    output_func("Proposed commands:")
    for index, proposal in enumerate(proposals, start=1):
        output_func(f"{index}. {proposal.command}")
    logger.log(
        "PROPOSE_COMMANDS",
        "Proposed command alternatives",
        commands=[proposal.command for proposal in proposals],
    )
    selected = _choose_proposal_index(
        len(proposals),
        "Choose a command proposal number, or type no to cancel:",
        logger,
        input_func,
        output_func,
    )
    if selected is None:
        logger.log("COMMAND_DENIED", "User declined command proposals", user_decision="no")
        return None
    return proposals[selected]


def _select_edit_proposal(
    proposals: list[EditProposal],
    logger: AgentLogger,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> EditProposal | None:
    if len(proposals) == 1:
        output_func("Proposed edit:")
        output_func(proposals[0].diff)
        logger.log("PROPOSE_EDIT", "Proposed edit", diff=proposals[0].diff)
        return proposals[0]

    output_func("Proposed edits:")
    for index, proposal in enumerate(proposals, start=1):
        output_func(f"{index}.")
        output_func(proposal.diff)
    logger.log(
        "PROPOSE_EDITS",
        "Proposed edit alternatives",
        diffs=[proposal.diff for proposal in proposals],
    )
    selected = _choose_proposal_index(
        len(proposals),
        "Choose an edit proposal number, or type no to cancel:",
        logger,
        input_func,
        output_func,
    )
    if selected is None:
        logger.log("EDIT_DENIED", "User declined edit proposals", user_decision="no")
        return None
    return proposals[selected]


def _choose_proposal_index(
    proposal_count: int,
    prompt: str,
    logger: AgentLogger,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
) -> int | None:
    while True:
        response = input_func(prompt + " ").strip().lower()
        if response in {"no", "n", "cancel"}:
            return None
        try:
            selected = int(response)
        except ValueError:
            selected = 0
        if 1 <= selected <= proposal_count:
            return selected - 1
        output_func(f"Please enter a number from 1 to {proposal_count}, or no.")
        logger.log(
            "INVALID_PROPOSAL_SELECTION",
            "User entered invalid proposal selection",
            response=response,
            proposal_count=proposal_count,
        )


def _resolve_existing_project_file(project_root: Path, user_path: str) -> str:
    candidate = Path(user_path)
    try:
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=True)
        else:
            resolved = (project_root / candidate).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Cannot read target file: {exc}") from exc
    if not _is_relative_to(resolved, project_root):
        raise ValueError(f"Read denied outside project root: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"Cannot edit non-file path: {user_path}")
    return resolved.relative_to(project_root).as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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
