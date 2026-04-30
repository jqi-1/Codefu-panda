import unittest

from local_agent.ai_connector import AIConnectorError
from local_agent.logger import AgentLogger
from local_agent.main import get_suggestions, run_menu_loop
from local_agent.models import ProjectSummary
from local_agent.permission_manager import PermissionManager
from local_agent.command_runner import CommandRunner
from tests.helpers import workspace


class FailingConnector:
    def generate(self, prompt: str) -> str:
        raise AIConnectorError("offline")


class StaticConnector:
    def __init__(self, output: str):
        self.output = output

    def generate(self, prompt: str) -> str:
        return self.output


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
            inputs = iter(["run", "quit"])
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

            self.assertIn("Could not generate a structured command proposal.", output)
            self.assertIn("ERROR", (root / "agent_history.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
