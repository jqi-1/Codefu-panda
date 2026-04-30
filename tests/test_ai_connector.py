import json
import unittest
from pathlib import Path

from local_agent.ai_connector import (
    AIConnectorError,
    build_prompt,
    deterministic_suggestions,
    parse_command,
    parse_commands,
    parse_edit,
    parse_edits,
    parse_suggestions,
)
from local_agent.models import ProjectSummary


class AIConnectorTests(unittest.TestCase):
    def _summary(self) -> ProjectSummary:
        return ProjectSummary(
            root=Path("."),
            primary_language="Python",
            secondary_languages=[],
            likely_entry_points=["main.py"],
            file_count=1,
            ignored_directory_count=0,
            tests_detected=True,
            dependency_files=["requirements.txt"],
        )

    def test_parse_suggestions_accepts_json_with_exactly_two_items(self):
        proposal = parse_suggestions(
            '{"suggestions":["Code quality: Improve names.","Project health: Add tests."]}'
        )

        self.assertEqual(2, len(proposal.suggestions))

    def test_parse_suggestions_rejects_wrong_count(self):
        with self.assertRaises(AIConnectorError):
            parse_suggestions('{"suggestions":["Only one."]}')

    def test_parse_command_requires_one_line(self):
        proposal = parse_command('{"command":"pytest"}')

        self.assertEqual("pytest", proposal.command)
        with self.assertRaises(AIConnectorError):
            parse_command('{"command":"pytest\\nmypy"}')

    def test_parse_commands_accepts_multiple_commands(self):
        proposals = parse_commands('{"commands":["pytest tests","python -m unittest"]}')

        self.assertEqual(["pytest tests", "python -m unittest"], [item.command for item in proposals])

    def test_parse_command_requires_json_schema(self):
        with self.assertRaises(AIConnectorError):
            parse_command("pytest")

    def test_parse_edit_accepts_json_unified_diff(self):
        diff = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b"
        proposal = parse_edit(
            json.dumps({"diff": diff})
        )

        self.assertIn("@@ -1 +1 @@", proposal.diff)

    def test_parse_edits_accepts_multiple_diffs(self):
        first = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b"
        second = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+c"

        proposals = parse_edits(json.dumps({"diffs": [first, second]}))

        self.assertEqual([first, second], [item.diff for item in proposals])

    def test_parse_edit_requires_json_schema(self):
        with self.assertRaises(AIConnectorError):
            parse_edit("```diff\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n```")

    def test_prompt_includes_user_task(self):
        prompt = build_prompt("run", self._summary(), user_task="run the test suite")

        self.assertIn("User task:", prompt)
        self.assertIn("run the test suite", prompt)
        self.assertIn('{"commands":["single-line command"]}', prompt)

    def test_edit_prompt_includes_target_file_path_and_contents(self):
        prompt = build_prompt(
            "edit",
            self._summary(),
            user_task="change the greeting",
            target_file_path="app.py",
            target_file_contents="print('hello')\n",
        )

        self.assertIn("Target file path: app.py", prompt)
        self.assertIn("print('hello')", prompt)
        self.assertIn('{"diffs":["unified diff"]}', prompt)

    def test_deterministic_suggestions_always_returns_exactly_two(self):
        summary = ProjectSummary(
            root=Path("."),
            primary_language="Unknown",
            secondary_languages=[],
            likely_entry_points=[],
            file_count=0,
            ignored_directory_count=0,
            tests_detected=False,
            dependency_files=[],
        )

        proposal = deterministic_suggestions(summary)

        self.assertEqual(2, len(proposal.suggestions))


if __name__ == "__main__":
    unittest.main()
