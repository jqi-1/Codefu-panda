import os
import unittest
from pathlib import Path

from local_agent.logger import AgentLogger
from local_agent.permission_manager import (
    DEFAULT_ALLOWED_COMMANDS,
    PROJECT_CODE_RISK_MESSAGE,
    PermissionManager,
)
from tests.helpers import workspace


class PermissionManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = workspace("permission")
        self.root = self.tmp.__enter__().resolve()
        self.logger = AgentLogger(self.root)
        self.manager = PermissionManager(self.root, self.logger)

    def tearDown(self):
        self.tmp.__exit__(None, None, None)

    def test_confirmation_reprompts_until_exact_yes_or_no(self):
        responses = iter(["maybe", " YES "])
        output = []

        approved = self.manager.confirm(
            "Run this command? (yes/no)",
            input_func=lambda prompt: next(responses),
            output_func=output.append,
        )

        self.assertTrue(approved)
        self.assertEqual(["Please answer exactly yes or no."], output)
        self.assertIn("INVALID_CONFIRMATION", (self.root / "agent_history.md").read_text())

    def test_command_validation_blocks_shell_metacharacters(self):
        result = self.manager.validate_command("pytest | cat")

        self.assertFalse(result.ok)
        self.assertEqual("COMMAND_BLOCKED", result.event_type)

    def test_command_validation_blocks_command_substitution(self):
        result = self.manager.validate_command("pytest $(cat secret)")

        self.assertFalse(result.ok)
        self.assertEqual("COMMAND_BLOCKED", result.event_type)

    def test_command_validation_blocks_backticks_inside_quotes(self):
        result = self.manager.validate_command('pytest "`cat secret`"')

        self.assertFalse(result.ok)
        self.assertEqual("COMMAND_BLOCKED", result.event_type)

    def test_command_validation_blocks_redirection_inside_quotes(self):
        result = self.manager.validate_command('pytest "a > b"')

        self.assertFalse(result.ok)
        self.assertEqual("COMMAND_BLOCKED", result.event_type)

    def test_command_validation_allows_safe_commands(self):
        (self.root / "tests").mkdir()

        result = self.manager.validate_command("pytest tests")

        self.assertTrue(result.ok)
        self.assertEqual(["pytest", "tests"], result.tokens)

    def test_command_validation_blocks_non_whitelisted_base(self):
        result = self.manager.validate_command("bash -c pytest")

        self.assertFalse(result.ok)
        self.assertIn("whitelist", result.message)

    def test_command_validation_blocks_destructive_patterns(self):
        result = self.manager.validate_command("git push --force origin main")

        self.assertFalse(result.ok)
        self.assertEqual("BLOCKED_DESTRUCTIVE_ACTION", result.event_type)

    def test_command_validation_blocks_rm_rf(self):
        result = self.manager.validate_command("rm -rf .")

        self.assertFalse(result.ok)
        self.assertEqual("BLOCKED_DESTRUCTIVE_ACTION", result.event_type)

    def test_command_validation_blocks_outside_paths(self):
        result = self.manager.validate_command("pytest ../outside")

        self.assertFalse(result.ok)
        self.assertEqual("SANDBOX_DENIED", result.event_type)

    def test_command_validation_blocks_nonexistent_absolute_outside_path(self):
        result = self.manager.validate_command("python /tmp/not_created_yet.py")

        self.assertFalse(result.ok)
        self.assertEqual("SANDBOX_DENIED", result.event_type)

    def test_command_validation_marks_risky_dependency_commands(self):
        result = self.manager.validate_command("npm install")

        self.assertTrue(result.ok)
        self.assertTrue(result.risky)
        self.assertIn("modify dependencies", result.risk_message)

    def test_command_validation_marks_project_defined_commands(self):
        result = self.manager.validate_command("npm test")

        self.assertTrue(result.ok)
        self.assertTrue(result.risky)
        self.assertEqual(PROJECT_CODE_RISK_MESSAGE, result.risk_message)

    def test_command_validation_blocks_global_installs(self):
        result = self.manager.validate_command("npm install -g eslint")

        self.assertFalse(result.ok)
        self.assertEqual("BLOCKED_DESTRUCTIVE_ACTION", result.event_type)

    def test_pip_is_not_allowed_by_default(self):
        self.assertNotIn("pip", DEFAULT_ALLOWED_COMMANDS)

        result = self.manager.validate_command("pip install pytest")

        self.assertFalse(result.ok)
        self.assertIn("whitelist", result.message)

    def test_python_m_pip_blocks_user_or_system_flags(self):
        user_result = self.manager.validate_command("python -m pip install --user pytest")
        system_result = self.manager.validate_command(
            "python -m pip install --break-system-packages pytest"
        )

        self.assertFalse(user_result.ok)
        self.assertEqual("BLOCKED_DESTRUCTIVE_ACTION", user_result.event_type)
        self.assertFalse(system_result.ok)
        self.assertEqual("BLOCKED_DESTRUCTIVE_ACTION", system_result.event_type)

    def test_command_validation_blocks_symlink_to_outside_root(self):
        with workspace("outside") as outside:
            outside_file = outside / "secret.txt"
            outside_file.write_text("secret", encoding="utf-8")
            link = self.root / "linked.txt"
            try:
                os.symlink(outside_file, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not available")

            result = self.manager.validate_command("pytest linked.txt")

            self.assertFalse(result.ok)
            self.assertEqual("SANDBOX_DENIED", result.event_type)

    def test_apply_unified_diff_success(self):
        target = self.root / "hello.txt"
        target.write_text("one\n", encoding="utf-8")
        diff = "\n".join(
            [
                "--- a/hello.txt",
                "+++ b/hello.txt",
                "@@ -1 +1 @@",
                "-one",
                "+two",
            ]
        )

        result = self.manager.apply_unified_diff(diff)

        self.assertTrue(result.ok)
        self.assertEqual("two\n", target.read_text(encoding="utf-8"))
        self.assertIn("EDIT_APPLIED", (self.root / "agent_history.md").read_text())

    def test_apply_unified_diff_reports_conflict(self):
        target = self.root / "hello.txt"
        target.write_text("changed\n", encoding="utf-8")
        diff = "\n".join(
            [
                "--- a/hello.txt",
                "+++ b/hello.txt",
                "@@ -1 +1 @@",
                "-one",
                "+two",
            ]
        )

        result = self.manager.apply_unified_diff(diff)

        self.assertFalse(result.ok)
        self.assertEqual("CONFLICT", result.event_type)
        self.assertEqual("changed\n", target.read_text(encoding="utf-8"))

    def test_apply_unified_diff_rejects_context_mismatch(self):
        target = self.root / "hello.txt"
        target.write_text("actual\n", encoding="utf-8")
        diff = "\n".join(
            [
                "--- a/hello.txt",
                "+++ b/hello.txt",
                "@@ -1 +1 @@",
                "-expected",
                "+new",
            ]
        )

        result = self.manager.apply_unified_diff(diff)

        self.assertFalse(result.ok)
        self.assertEqual("CONFLICT", result.event_type)

    def test_apply_unified_diff_blocks_deletion(self):
        diff = "\n".join(
            [
                "--- a/hello.txt",
                "+++ /dev/null",
                "@@ -1 +0,0 @@",
                "-one",
            ]
        )

        result = self.manager.apply_unified_diff(diff)

        self.assertFalse(result.ok)
        self.assertEqual("BLOCKED_DESTRUCTIVE_ACTION", result.event_type)

    def test_apply_unified_diff_blocks_outside_path(self):
        diff = "\n".join(
            [
                "--- /dev/null",
                "+++ b/../outside.txt",
                "@@ -0,0 +1 @@",
                "+hello",
            ]
        )

        result = self.manager.apply_unified_diff(diff)

        self.assertFalse(result.ok)
        self.assertEqual("SANDBOX_DENIED", result.event_type)

    def test_apply_unified_diff_blocks_multiple_files(self):
        diff = "\n".join(
            [
                "--- a/one.txt",
                "+++ b/one.txt",
                "@@ -0,0 +1 @@",
                "+one",
                "--- a/two.txt",
                "+++ b/two.txt",
                "@@ -0,0 +1 @@",
                "+two",
            ]
        )

        result = self.manager.apply_unified_diff(diff)

        self.assertFalse(result.ok)
        self.assertEqual("EDIT_FAILED", result.event_type)

    def test_apply_unified_diff_blocks_binary_file(self):
        target = self.root / "data.bin"
        target.write_bytes(b"a\0b")
        diff = "\n".join(
            [
                "--- a/data.bin",
                "+++ b/data.bin",
                "@@ -1 +1 @@",
                "-a",
                "+b",
            ]
        )

        result = self.manager.apply_unified_diff(diff)

        self.assertFalse(result.ok)
        self.assertEqual("EDIT_FAILED", result.event_type)


if __name__ == "__main__":
    unittest.main()
