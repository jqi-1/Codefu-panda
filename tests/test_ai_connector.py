import unittest
from pathlib import Path

from local_agent.ai_connector import (
    AIConnectorError,
    deterministic_suggestions,
    parse_command,
    parse_edit,
    parse_suggestions,
)
from local_agent.models import ProjectSummary


class AIConnectorTests(unittest.TestCase):
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
            parse_command("pytest\nmypy")

    def test_parse_edit_accepts_fenced_unified_diff(self):
        proposal = parse_edit(
            "```diff\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n```"
        )

        self.assertIn("@@ -1 +1 @@", proposal.diff)

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
