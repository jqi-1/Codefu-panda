import unittest

from local_agent.file_watcher import FileTooLargeError, ProjectScanner, SandboxError
from tests.helpers import workspace


class FileWatcherTests(unittest.TestCase):
    def test_scan_detects_languages_dependencies_tests_and_ignored_dirs(self):
        with workspace("scan") as root:
            (root / ".git").mkdir()
            (root / "node_modules").mkdir()
            (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_main.py").write_text("def test_x(): pass\n", encoding="utf-8")

            summary = ProjectScanner(root).scan()

            self.assertEqual("Python", summary.primary_language)
            self.assertIn("Markdown", summary.secondary_languages)
            self.assertIn("main.py", summary.likely_entry_points)
            self.assertIn("requirements.txt", summary.dependency_files)
            self.assertTrue(summary.tests_detected)
            self.assertEqual(2, summary.ignored_directory_count)
            self.assertEqual(4, summary.file_count)

    def test_scan_ignores_agent_history_log(self):
        with workspace("scan") as root:
            (root / "agent_history.md").write_text("# Log\n", encoding="utf-8")

            summary = ProjectScanner(root).scan()

            self.assertEqual("Unknown", summary.primary_language)
            self.assertEqual(0, summary.file_count)

    def test_read_text_file_denies_outside_root(self):
        with workspace("read") as root:
            scanner = ProjectScanner(root)

            with self.assertRaises(SandboxError):
                scanner.read_text_file("../outside.txt")

    def test_read_text_file_rejects_binary(self):
        with workspace("binary") as root:
            (root / "data.bin").write_bytes(b"a\0b")
            scanner = ProjectScanner(root)

            with self.assertRaises(UnicodeDecodeError):
                scanner.read_text_file("data.bin")

    def test_read_text_file_rejects_large_file(self):
        with workspace("large") as root:
            (root / "big.txt").write_text("abcd", encoding="utf-8")
            scanner = ProjectScanner(root)

            with self.assertRaises(FileTooLargeError):
                scanner.read_text_file("big.txt", max_bytes=3)


if __name__ == "__main__":
    unittest.main()
