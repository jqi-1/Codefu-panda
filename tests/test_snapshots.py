import json
import os
import unittest
from pathlib import Path

from local_agent.logger import AgentLogger
from local_agent.permission_manager import PermissionManager
from local_agent.snapshots import SnapshotError, list_snapshots, restore_snapshot
from tests.helpers import workspace


def replace_diff(path: str, before: str, after: str) -> str:
    return "\n".join(
        [
            f"--- a/{path}",
            f"+++ b/{path}",
            "@@ -1 +1 @@",
            f"-{before}",
            f"+{after}",
        ]
    )


class SnapshotTests(unittest.TestCase):
    def test_snapshot_created_before_real_edit_and_contains_original(self):
        with workspace("snapshot") as root:
            target = root / "hello.txt"
            target.write_text("one\n", encoding="utf-8")
            manager = PermissionManager(root, AgentLogger(root))

            result = manager.apply_unified_diff(replace_diff("hello.txt", "one", "two"))

            self.assertTrue(result.ok)
            snapshots = list_snapshots(root)
            self.assertEqual(1, len(snapshots))
            snapshot_file = snapshots[0].path / "files" / "hello.txt"
            self.assertEqual("one\n", snapshot_file.read_text(encoding="utf-8"))

    def test_snapshot_metadata_preserves_nested_relative_path(self):
        with workspace("snapshot") as root:
            nested = root / "pkg"
            nested.mkdir()
            target = nested / "hello.txt"
            target.write_text("one\n", encoding="utf-8")
            manager = PermissionManager(root, AgentLogger(root))

            result = manager.apply_unified_diff(replace_diff("pkg/hello.txt", "one", "two"))

            self.assertTrue(result.ok)
            metadata = json.loads(
                (list_snapshots(root)[0].path / "metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("pkg/hello.txt", metadata["files"][0]["path"])
            self.assertEqual("files/pkg/hello.txt", metadata["files"][0]["snapshot_path"])

    def test_no_snapshot_for_invalid_diff(self):
        with workspace("snapshot") as root:
            (root / "hello.txt").write_text("one\n", encoding="utf-8")
            manager = PermissionManager(root, AgentLogger(root))

            result = manager.apply_unified_diff("not a diff")

            self.assertFalse(result.ok)
            self.assertEqual([], list_snapshots(root))

    def test_no_snapshot_for_dry_run(self):
        with workspace("snapshot") as root:
            target = root / "hello.txt"
            target.write_text("one\n", encoding="utf-8")
            manager = PermissionManager(root, AgentLogger(root))

            result = manager.apply_unified_diff(
                replace_diff("hello.txt", "one", "two"),
                dry_run=True,
            )

            self.assertTrue(result.ok)
            self.assertEqual("one\n", target.read_text(encoding="utf-8"))
            self.assertEqual([], list_snapshots(root))

    def test_symlink_escape_is_not_snapshotted(self):
        with workspace("snapshot") as root, workspace("outside") as outside:
            outside_file = outside / "secret.txt"
            outside_file.write_text("secret\n", encoding="utf-8")
            link = root / "linked.txt"
            try:
                os.symlink(outside_file, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is not available")
            manager = PermissionManager(root, AgentLogger(root))

            result = manager.apply_unified_diff(replace_diff("linked.txt", "secret", "open"))

            self.assertFalse(result.ok)
            self.assertEqual("SANDBOX_DENIED", result.event_type)
            self.assertEqual([], list_snapshots(root))

    def test_missing_original_file_is_recorded_and_restored_by_removal(self):
        with workspace("snapshot") as root:
            manager = PermissionManager(root, AgentLogger(root))
            diff = "\n".join(
                [
                    "--- /dev/null",
                    "+++ b/new.txt",
                    "@@ -0,0 +1 @@",
                    "+hello",
                ]
            )

            result = manager.apply_unified_diff(diff)
            restore_result = restore_snapshot(root)

            self.assertTrue(result.ok)
            self.assertFalse((root / "new.txt").exists())
            metadata = json.loads(
                (restore_result.snapshot.path / "metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(metadata["files"][0]["original_missing"])
            self.assertEqual([Path("new.txt")], restore_result.removed_paths)

    def test_restore_most_recent_snapshot_restores_original_content(self):
        with workspace("snapshot") as root:
            target = root / "hello.txt"
            target.write_text("one\n", encoding="utf-8")
            manager = PermissionManager(root, AgentLogger(root))
            manager.apply_unified_diff(replace_diff("hello.txt", "one", "two"))

            result = restore_snapshot(root)

            self.assertEqual("one\n", target.read_text(encoding="utf-8"))
            self.assertEqual([Path("hello.txt")], result.restored_paths)

    def test_restore_specific_snapshot(self):
        with workspace("snapshot") as root:
            target = root / "hello.txt"
            target.write_text("one\n", encoding="utf-8")
            manager = PermissionManager(root, AgentLogger(root))
            first = manager.apply_unified_diff(replace_diff("hello.txt", "one", "two"))
            second = manager.apply_unified_diff(replace_diff("hello.txt", "two", "three"))

            restore_snapshot(root, first.snapshot_id)

            self.assertNotEqual(first.snapshot_id, second.snapshot_id)
            self.assertEqual("one\n", target.read_text(encoding="utf-8"))

    def test_restore_fails_gracefully_when_no_snapshots_exist(self):
        with workspace("snapshot") as root:
            with self.assertRaises(SnapshotError):
                restore_snapshot(root)

    def test_restore_refuses_path_escape(self):
        with workspace("snapshot") as root:
            snapshot_dir = root / ".codefu-panda" / "snapshots" / "bad"
            snapshot_dir.mkdir(parents=True)
            (snapshot_dir / "metadata.json").write_text(
                json.dumps({"created_at": "now", "files": [{"path": "../outside.txt"}]}),
                encoding="utf-8",
            )

            with self.assertRaises(SnapshotError):
                restore_snapshot(root, "bad")


if __name__ == "__main__":
    unittest.main()
