import unittest

from local_agent.ai_connector import AIConnectorError
from local_agent.command_runner import CommandRunner
from local_agent.logger import AgentLogger
from local_agent.main import get_suggestions, handle_edit, handle_run, run_menu_loop
from local_agent.models import (
    CommandProposal,
    CommandResult,
    EditProposal,
    EditResult,
    ProjectSummary,
)
from local_agent.permission_manager import PROJECT_CODE_RISK_MESSAGE, PermissionManager
from local_agent.snapshots import list_snapshots
from tests.helpers import workspace


class FailingConnector:
    def generate(self, prompt: str) -> str:
        raise AIConnectorError("offline")


class StaticConnector:
    def __init__(self, output: str):
        self.output = output

    def generate(self, prompt: str) -> str:
        return self.output


class RecordingScanner:
    def __init__(self, project_root, contents: str):
        self.project_root = project_root
        self.contents = contents
        self.reads = []

    def read_text_file(self, relative_path: str) -> str:
        self.reads.append(relative_path)
        return self.contents


class RecordingEditConnector:
    def __init__(self, proposal: EditProposal):
        self.proposal = proposal
        self.user_task = None
        self.target_file_path = None
        self.target_file_contents = None

    def propose_edit(
        self,
        summary: ProjectSummary,
        user_task: str,
        target_file_path: str,
        target_file_contents: str,
    ) -> EditProposal:
        self.user_task = user_task
        self.target_file_path = target_file_path
        self.target_file_contents = target_file_contents
        return self.proposal


class RecordingCommandListConnector:
    def __init__(self, proposals: list[CommandProposal]):
        self.proposals = proposals

    def propose_commands(
        self,
        summary: ProjectSummary,
        user_task: str,
    ) -> list[CommandProposal]:
        return self.proposals


class RecordingEditListConnector:
    def __init__(self, proposals: list[EditProposal]):
        self.proposals = proposals

    def propose_edits(
        self,
        summary: ProjectSummary,
        user_task: str,
        target_file_path: str,
        target_file_contents: str,
    ) -> list[EditProposal]:
        return self.proposals


class RecordingPermissionManager:
    def __init__(self):
        self.applied_diff = None

    def confirm(self, prompt, input_func=input, output_func=print):
        return True

    def apply_unified_diff(self, diff: str) -> EditResult:
        self.applied_diff = diff
        return EditResult(True, "Edit applied.", "EDIT_APPLIED")


class RecordingRunner:
    def __init__(self):
        self.commands = []

    def run(self, command: str, tokens: list[str]) -> CommandResult:
        self.commands.append((command, tokens))
        return CommandResult(command, "", "", 0, False)


class MainFlowTests(unittest.TestCase):
    def test_get_suggestions_falls_back_when_connector_fails(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Unknown",
                secondary_languages=[],
                likely_entry_points=[],
                file_count=0,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )

            proposal = get_suggestions(FailingConnector(), summary, logger)

            self.assertEqual(2, len(proposal.suggestions))
            self.assertIn("ERROR", (root / "agent_history.md").read_text(encoding="utf-8"))

    def test_menu_invalid_input_then_quit_logs_both(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Unknown",
                secondary_languages=[],
                likely_entry_points=[],
                file_count=0,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )
            inputs = iter(["wat", "quit"])
            output = []

            run_menu_loop(
                StaticConnector('{"suggestions":["Code quality: x","Project health: y"]}'),
                summary,
                PermissionManager(root, logger),
                CommandRunner(root),
                logger,
                input_func=lambda prompt: next(inputs),
                output_func=output.append,
            )

            history = (root / "agent_history.md").read_text(encoding="utf-8")
            self.assertIn("INVALID_MENU_INPUT", history)
            self.assertIn("SHUTDOWN", history)
            self.assertIn("Invalid option. Type suggest, run, edit, or quit.", output)

    def test_run_flow_fails_closed_on_bad_ai_output(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Unknown",
                secondary_languages=[],
                likely_entry_points=[],
                file_count=0,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )
            inputs = iter(["run", "run tests", "quit"])
            output = []

            run_menu_loop(
                StaticConnector("pytest\nmypy"),
                summary,
                PermissionManager(root, logger),
                CommandRunner(root),
                logger,
                input_func=lambda prompt: next(inputs),
                output_func=output.append,
            )

            self.assertIn("Could not generate structured command proposals.", output)
            self.assertIn("ERROR", (root / "agent_history.md").read_text(encoding="utf-8"))

    def test_run_flow_warns_before_project_defined_command(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="JavaScript",
                secondary_languages=[],
                likely_entry_points=["package.json"],
                file_count=1,
                ignored_directory_count=0,
                tests_detected=True,
                dependency_files=["package.json"],
            )
            inputs = iter(["run npm tests", "no"])
            prompts = []
            output = []

            def input_func(prompt):
                prompts.append(prompt)
                return next(inputs)

            handle_run(
                StaticConnector('{"type":"command","command":"npm test"}'),
                summary,
                PermissionManager(root, logger),
                RecordingRunner(),
                logger,
                input_func=input_func,
                output_func=output.append,
            )

            self.assertTrue(any(PROJECT_CODE_RISK_MESSAGE in prompt for prompt in prompts))

    def test_run_flow_displays_selected_command_before_confirmation(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Python",
                secondary_languages=[],
                likely_entry_points=[],
                file_count=0,
                ignored_directory_count=0,
                tests_detected=True,
                dependency_files=[],
            )
            inputs = iter(["run tests", "yes"])
            events = []
            runner = RecordingRunner()

            def input_func(prompt):
                events.append(("prompt", prompt))
                return next(inputs)

            def output_func(text):
                events.append(("output", text))

            handle_run(
                StaticConnector('{"type":"command","command":"python -m unittest"}'),
                summary,
                PermissionManager(root, logger),
                runner,
                logger,
                input_func=input_func,
                output_func=output_func,
            )

            selected_index = events.index(("output", "Selected command:"))
            confirmation_index = next(
                index
                for index, event in enumerate(events)
                if event[0] == "prompt" and "Run this command?" in event[1]
            )
            self.assertLess(selected_index, confirmation_index)
            self.assertEqual(
                [("python -m unittest", ["python", "-m", "unittest"])],
                runner.commands,
            )

    def test_run_flow_blocks_invalid_selected_command_without_confirmation(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Unknown",
                secondary_languages=[],
                likely_entry_points=[],
                file_count=0,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )
            inputs = iter(["restore changes"])
            prompts = []
            output = []
            runner = RecordingRunner()

            def input_func(prompt):
                prompts.append(prompt)
                return next(inputs)

            handle_run(
                StaticConnector('{"type":"command","command":"git restore ."}'),
                summary,
                PermissionManager(root, logger),
                runner,
                logger,
                input_func=input_func,
                output_func=output.append,
            )

            self.assertIn("Selected command:", output)
            self.assertIn("git restore .", output)
            self.assertTrue(any("git restore" in item for item in output))
            self.assertFalse(any("Run this command?" in prompt for prompt in prompts))
            self.assertEqual([], runner.commands)

    def test_run_flow_lets_user_choose_from_multiple_commands(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Python",
                secondary_languages=[],
                likely_entry_points=[],
                file_count=0,
                ignored_directory_count=0,
                tests_detected=True,
                dependency_files=[],
            )
            inputs = iter(["run tests", "2", "yes"])
            output = []
            runner = RecordingRunner()

            handle_run(
                RecordingCommandListConnector(
                    [
                        CommandProposal("pytest tests"),
                        CommandProposal("python -m unittest"),
                    ]
                ),
                summary,
                PermissionManager(root, logger),
                runner,
                logger,
                input_func=lambda prompt: next(inputs),
                output_func=output.append,
            )

            self.assertEqual(
                [("python -m unittest", ["python", "-m", "unittest"])],
                runner.commands,
            )
            self.assertIn("Proposed commands:", output)

    def test_edit_flow_reads_target_and_applies_only_unified_diff(self):
        with workspace("main") as root:
            target = root / "app.py"
            target.write_text("print('hello')\n", encoding="utf-8")
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Python",
                secondary_languages=[],
                likely_entry_points=["app.py"],
                file_count=1,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )
            diff = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('hi')",
                ]
            )
            scanner = RecordingScanner(root, "print('hello')\n")
            connector = RecordingEditConnector(EditProposal(diff))
            permission_manager = RecordingPermissionManager()
            inputs = iter(["change the greeting", "app.py"])
            output = []

            handle_edit(
                connector,
                summary,
                scanner,
                permission_manager,
                logger,
                input_func=lambda prompt: next(inputs),
                output_func=output.append,
            )

            self.assertEqual(["app.py"], scanner.reads)
            self.assertEqual("change the greeting", connector.user_task)
            self.assertEqual("app.py", connector.target_file_path)
            self.assertEqual("print('hello')\n", connector.target_file_contents)
            self.assertEqual(diff, permission_manager.applied_diff)
            self.assertEqual("print('hello')\n", target.read_text(encoding="utf-8"))

    def test_edit_flow_lets_user_choose_from_multiple_diffs(self):
        with workspace("main") as root:
            target = root / "app.py"
            target.write_text("print('hello')\n", encoding="utf-8")
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Python",
                secondary_languages=[],
                likely_entry_points=["app.py"],
                file_count=1,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )
            first = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('hey')",
                ]
            )
            second = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('hi')",
                ]
            )
            scanner = RecordingScanner(root, "print('hello')\n")
            permission_manager = RecordingPermissionManager()
            inputs = iter(["change the greeting", "app.py", "2"])
            output = []

            handle_edit(
                RecordingEditListConnector([EditProposal(first), EditProposal(second)]),
                summary,
                scanner,
                permission_manager,
                logger,
                input_func=lambda prompt: next(inputs),
                output_func=output.append,
            )

            self.assertEqual(second, permission_manager.applied_diff)
            self.assertIn("Proposed edits:", output)

    def test_dry_run_command_validates_but_does_not_run(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Python",
                secondary_languages=[],
                likely_entry_points=[],
                file_count=0,
                ignored_directory_count=0,
                tests_detected=True,
                dependency_files=[],
            )
            runner = RecordingRunner()
            output = []

            handle_run(
                StaticConnector('{"type":"command","command":"python -m unittest"}'),
                summary,
                PermissionManager(root, logger),
                runner,
                logger,
                input_func=lambda prompt: "run tests",
                output_func=output.append,
                dry_run=True,
            )

            self.assertEqual([], runner.commands)
            self.assertIn("Dry-run: command would be allowed.", output)

    def test_dry_run_blocked_command_reports_blocked(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Unknown",
                secondary_languages=[],
                likely_entry_points=[],
                file_count=0,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )
            output = []

            handle_run(
                StaticConnector('{"type":"command","command":"git restore ."}'),
                summary,
                PermissionManager(root, logger),
                RecordingRunner(),
                logger,
                input_func=lambda prompt: "restore changes",
                output_func=output.append,
                dry_run=True,
            )

            self.assertTrue(any("git restore" in item for item in output))

    def test_dry_run_risky_command_reports_confirmation_needed(self):
        with workspace("main") as root:
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="JavaScript",
                secondary_languages=[],
                likely_entry_points=["package.json"],
                file_count=1,
                ignored_directory_count=0,
                tests_detected=True,
                dependency_files=["package.json"],
            )
            output = []

            handle_run(
                StaticConnector('{"type":"command","command":"npm test"}'),
                summary,
                PermissionManager(root, logger),
                RecordingRunner(),
                logger,
                input_func=lambda prompt: "run npm tests",
                output_func=output.append,
                dry_run=True,
            )

            self.assertIn(
                "Dry-run: command is risky and would require confirmation.",
                output,
            )
            self.assertIn(PROJECT_CODE_RISK_MESSAGE, output)

    def test_dry_run_edit_validates_but_does_not_modify_file_or_snapshot(self):
        with workspace("main") as root:
            target = root / "app.py"
            target.write_text("print('hello')\n", encoding="utf-8")
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Python",
                secondary_languages=[],
                likely_entry_points=["app.py"],
                file_count=1,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )
            diff = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('hi')",
                ]
            )
            scanner = RecordingScanner(root, "print('hello')\n")
            output = []
            inputs = iter(["change greeting", "app.py"])

            handle_edit(
                RecordingEditConnector(EditProposal(diff)),
                summary,
                scanner,
                PermissionManager(root, logger),
                logger,
                input_func=lambda prompt: next(inputs),
                output_func=output.append,
                dry_run=True,
            )

            self.assertEqual("print('hello')\n", target.read_text(encoding="utf-8"))
            self.assertEqual([], list_snapshots(root))
            self.assertIn("Dry-run: edit would modify app.py.", output)

    def test_dry_run_edit_with_invalid_diff_fails(self):
        with workspace("main") as root:
            target = root / "app.py"
            target.write_text("print('hello')\n", encoding="utf-8")
            logger = AgentLogger(root)
            summary = ProjectSummary(
                root=root,
                primary_language="Python",
                secondary_languages=[],
                likely_entry_points=["app.py"],
                file_count=1,
                ignored_directory_count=0,
                tests_detected=False,
                dependency_files=[],
            )
            scanner = RecordingScanner(root, "print('hello')\n")
            output = []
            inputs = iter(["change greeting", "app.py"])

            handle_edit(
                RecordingEditConnector(EditProposal("not a diff")),
                summary,
                scanner,
                PermissionManager(root, logger),
                logger,
                input_func=lambda prompt: next(inputs),
                output_func=output.append,
                dry_run=True,
            )

            self.assertEqual("print('hello')\n", target.read_text(encoding="utf-8"))
            self.assertEqual([], list_snapshots(root))
            self.assertIn("Unified diff must start each file with `--- `.", output)


if __name__ == "__main__":
    unittest.main()
