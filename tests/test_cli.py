import io
import unittest
from contextlib import redirect_stdout

from local_agent.logger import AgentLogger
from local_agent.main import main
from local_agent.permission_manager import PermissionManager
from tests.helpers import workspace


class CliTests(unittest.TestCase):
    def test_summarize_command_is_read_only(self):
        with workspace("cli") as root:
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main([str(root), "summarize"])

            self.assertEqual(0, exit_code)
            self.assertIn("Repository Summary", output.getvalue())
            self.assertFalse((root / "agent_history.md").exists())

    def test_restore_command_restores_most_recent_snapshot(self):
        with workspace("cli") as root:
            target = root / "hello.txt"
            target.write_text("one\n", encoding="utf-8")
            manager = PermissionManager(root, AgentLogger(root))
            diff = "\n".join(
                [
                    "--- a/hello.txt",
                    "+++ b/hello.txt",
                    "@@ -1 +1 @@",
                    "-one",
                    "+two",
                ]
            )
            manager.apply_unified_diff(diff)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main([str(root), "restore"])

            self.assertEqual(0, exit_code)
            self.assertEqual("one\n", target.read_text(encoding="utf-8"))
            self.assertIn("Restored snapshot:", output.getvalue())

    def test_restore_command_fails_gracefully_without_snapshots(self):
        with workspace("cli") as root:
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main([str(root), "restore"])

            self.assertEqual(1, exit_code)
            self.assertIn("No snapshots exist", output.getvalue())


if __name__ == "__main__":
    unittest.main()
