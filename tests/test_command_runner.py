import sys
import unittest

from local_agent.command_runner import CommandRunner
from tests.helpers import workspace


class CommandRunnerTests(unittest.TestCase):
    def test_run_captures_stdout_stderr_and_exit_code(self):
        with workspace("runner") as root:
            runner = CommandRunner(root, timeout_seconds=5)

            result = runner.run(
                "python -c print",
                [
                    sys.executable,
                    "-c",
                    "import sys; print('out'); print('err', file=sys.stderr)",
                ],
            )

            self.assertFalse(result.timed_out)
            self.assertEqual(0, result.exit_code)
            self.assertIn("out", result.stdout)
            self.assertIn("err", result.stderr)

    def test_run_reports_timeout(self):
        with workspace("runner") as root:
            runner = CommandRunner(root, timeout_seconds=1)

            result = runner.run(
                "python -c sleep",
                [sys.executable, "-c", "import time; time.sleep(5)"],
            )

            self.assertTrue(result.timed_out)
            self.assertIsNone(result.exit_code)


if __name__ == "__main__":
    unittest.main()
