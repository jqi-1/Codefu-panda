import unittest

from local_agent.repo_summary import summarize_repo
from tests.helpers import workspace


class RepoSummaryTests(unittest.TestCase):
    def test_summary_detects_pyproject_and_tests_directory(self):
        with workspace("summary") as root:
            (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (root / "tests").mkdir()

            summary = summarize_repo(root)

            self.assertIn("pyproject.toml", summary.key_files)
            self.assertTrue(summary.tests_directory_exists)
            self.assertIn("Python package detected", summary.language_indicators)

    def test_summary_counts_file_extensions(self):
        with workspace("summary") as root:
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
            (root / "README.md").write_text("# hi\n", encoding="utf-8")

            summary = summarize_repo(root)

            self.assertEqual(1, summary.extension_counts[".py"])
            self.assertEqual(1, summary.extension_counts[".md"])

    def test_summary_does_not_include_git_or_snapshots(self):
        with workspace("summary") as root:
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
            snapshot_dir = root / ".codefu-panda" / "snapshots" / "one"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "metadata.json").write_text("{}\n", encoding="utf-8")
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")

            summary = summarize_repo(root)
            text = summary.to_display_text()

            self.assertTrue(summary.git_repo_exists)
            self.assertNotIn(".git/config", text)
            self.assertNotIn("metadata.json", text)
            self.assertEqual(1, summary.extension_counts[".py"])

    def test_summary_handles_empty_repo(self):
        with workspace("summary") as root:
            summary = summarize_repo(root)

            self.assertEqual({}, summary.extension_counts)
            self.assertEqual([], summary.key_files)
            self.assertIn("No dependency manifest found.", summary.notes)


if __name__ == "__main__":
    unittest.main()
