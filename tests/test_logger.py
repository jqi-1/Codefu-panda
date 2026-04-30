import os
import unittest
from pathlib import Path

from local_agent.logger import AgentLogger, LoggerError
from tests.helpers import workspace


class LoggerTests(unittest.TestCase):
    def test_logger_appends_entries_without_truncating(self):
        with workspace("logger") as root:
            logger = AgentLogger(root)

            logger.log("STARTUP", "one")
            logger.log("SHUTDOWN", "two")

            text = (root / "agent_history.md").read_text(encoding="utf-8")
            self.assertIn("STARTUP", text)
            self.assertIn("SHUTDOWN", text)
            self.assertLess(text.index("STARTUP"), text.index("SHUTDOWN"))

    def test_logger_refuses_symlink_outside_root(self):
        with workspace("logger") as root, workspace("outside") as outside:
            outside_log = outside / "agent_history.md"
            outside_log.write_text("outside", encoding="utf-8")
            try:
                os.symlink(outside_log, root / "agent_history.md")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not available")

            with self.assertRaises(LoggerError):
                AgentLogger(root)


if __name__ == "__main__":
    unittest.main()
