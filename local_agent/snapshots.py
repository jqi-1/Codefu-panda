"""Lightweight edit snapshots and restore support."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SNAPSHOT_ROOT = ".codefu-panda"
SNAPSHOT_DIR = "snapshots"


class SnapshotError(RuntimeError):
    """Raised when a snapshot or restore would leave the project sandbox."""


@dataclass(frozen=True)
class Snapshot:
    id: str
    path: Path


@dataclass(frozen=True)
class RestoreResult:
    snapshot: Snapshot
    restored_paths: list[Path]
    removed_paths: list[Path]


@dataclass(frozen=True)
class _SnapshotFilePlan:
    relative_path: Path
    source_path: Path | None
    original_missing: bool


def create_snapshot(project_root: Path, relative_paths: list[Path]) -> Snapshot:
    root = project_root.resolve(strict=True)
    file_plans = [_plan_snapshot_file(root, relative_path) for relative_path in relative_paths]
    snapshots_dir = _snapshots_dir(root)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    _assert_inside(snapshots_dir.resolve(strict=True), root)

    snapshot_id = _new_snapshot_id(snapshots_dir)
    snapshot_dir = snapshots_dir / snapshot_id
    files_dir = snapshot_dir / "files"
    files_dir.mkdir(parents=True)

    created_at = _metadata_timestamp()
    entries: list[dict[str, object]] = []
    for file_plan in file_plans:
        normalized = file_plan.relative_path
        entry: dict[str, object] = {
            "path": normalized.as_posix(),
            "original_missing": file_plan.original_missing,
        }

        if file_plan.source_path is not None:
            snapshot_relative = Path("files") / normalized
            snapshot_target = snapshot_dir / snapshot_relative
            snapshot_target.parent.mkdir(parents=True, exist_ok=True)
            snapshot_target.write_bytes(file_plan.source_path.read_bytes())
            entry["snapshot_path"] = snapshot_relative.as_posix()
            entry["original_missing"] = False

        entries.append(entry)

    metadata = {
        "created_at": created_at,
        "files": entries,
    }
    (snapshot_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return Snapshot(id=snapshot_id, path=snapshot_dir)


def _plan_snapshot_file(root: Path, relative_path: Path) -> _SnapshotFilePlan:
    normalized = _normalize_relative_path(relative_path)
    target = root / normalized
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise SnapshotError(f"Refusing to snapshot symlink path: {normalized}")
        resolved_target = target.resolve(strict=True)
        _assert_inside(resolved_target, root)
        if not resolved_target.is_file():
            raise SnapshotError(f"Refusing to snapshot non-file path: {normalized}")
        return _SnapshotFilePlan(
            relative_path=normalized,
            source_path=resolved_target,
            original_missing=False,
        )

    parent = target.parent.resolve(strict=True)
    _assert_inside(parent, root)
    return _SnapshotFilePlan(
        relative_path=normalized,
        source_path=None,
        original_missing=True,
    )


def list_snapshots(project_root: Path) -> list[Snapshot]:
    root = project_root.resolve(strict=True)
    snapshots_dir = _snapshots_dir(root)
    if not snapshots_dir.exists():
        return []
    _assert_inside(snapshots_dir.resolve(strict=True), root)
    snapshots: list[Snapshot] = []
    for child in snapshots_dir.iterdir():
        if child.is_dir() and (child / "metadata.json").is_file():
            snapshots.append(Snapshot(id=child.name, path=child))
    return sorted(snapshots, key=lambda snapshot: snapshot.id)


def restore_snapshot(project_root: Path, snapshot_id: str | None = None) -> RestoreResult:
    root = project_root.resolve(strict=True)
    snapshots = list_snapshots(root)
    if not snapshots:
        raise SnapshotError("No snapshots exist for this project")

    if snapshot_id is None:
        snapshot = snapshots[-1]
    else:
        snapshot = _select_snapshot(root, snapshot_id)

    metadata_path = snapshot.path / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"Snapshot metadata is invalid: {exc}") from exc

    files = metadata.get("files")
    if not isinstance(files, list):
        raise SnapshotError("Snapshot metadata is missing a valid files list")

    restored_paths: list[Path] = []
    removed_paths: list[Path] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise SnapshotError("Snapshot metadata contains an invalid file entry")
        path_value = entry.get("path")
        if not isinstance(path_value, str):
            raise SnapshotError("Snapshot metadata file entry is missing a path")
        relative_path = _normalize_relative_path(Path(path_value))
        destination = root / relative_path
        original_missing = entry.get("original_missing") is True

        if original_missing:
            _remove_created_file(root, destination)
            removed_paths.append(relative_path)
            continue

        snapshot_path_value = entry.get("snapshot_path")
        if not isinstance(snapshot_path_value, str):
            raise SnapshotError("Snapshot metadata file entry is missing snapshot_path")
        snapshot_source = (snapshot.path / snapshot_path_value).resolve(strict=True)
        _assert_inside(snapshot_source, snapshot.path.resolve(strict=True))
        if not snapshot_source.is_file():
            raise SnapshotError(f"Snapshot file is missing: {snapshot_path_value}")

        _restore_file(root, destination, snapshot_source)
        restored_paths.append(relative_path)

    return RestoreResult(
        snapshot=snapshot,
        restored_paths=restored_paths,
        removed_paths=removed_paths,
    )


def _snapshots_dir(root: Path) -> Path:
    agent_dir = root / SNAPSHOT_ROOT
    if agent_dir.exists() or agent_dir.is_symlink():
        _assert_inside(agent_dir.resolve(strict=True), root)
        if not agent_dir.is_dir():
            raise SnapshotError(f"Snapshot root is not a directory: {agent_dir}")
    snapshots_dir = agent_dir / SNAPSHOT_DIR
    if snapshots_dir.exists() or snapshots_dir.is_symlink():
        _assert_inside(snapshots_dir.resolve(strict=True), root)
        if not snapshots_dir.is_dir():
            raise SnapshotError(f"Snapshot directory is not a directory: {snapshots_dir}")
    return snapshots_dir


def _new_snapshot_id(snapshots_dir: Path) -> str:
    base_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    candidate = base_id
    counter = 1
    while (snapshots_dir / candidate).exists():
        candidate = f"{base_id}-{counter:03d}"
        counter += 1
    return candidate


def _metadata_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _select_snapshot(root: Path, snapshot_id: str) -> Snapshot:
    if "/" in snapshot_id or "\\" in snapshot_id or snapshot_id in {"", ".", ".."}:
        raise SnapshotError("Snapshot id must be a directory name, not a path")
    snapshots_dir = _snapshots_dir(root)
    try:
        snapshot_path = (snapshots_dir / snapshot_id).resolve(strict=True)
    except OSError as exc:
        raise SnapshotError(f"Snapshot does not exist: {snapshot_id}") from exc
    _assert_inside(snapshot_path, snapshots_dir.resolve(strict=True))
    if not (snapshot_path / "metadata.json").is_file():
        raise SnapshotError(f"Snapshot does not exist: {snapshot_id}")
    return Snapshot(id=snapshot_id, path=snapshot_path)


def _normalize_relative_path(path: Path) -> Path:
    if path.is_absolute():
        raise SnapshotError(f"Snapshot path must be project-relative: {path}")
    normalized = Path(*path.parts)
    if normalized == Path(".") or any(part in {"", ".", ".."} for part in normalized.parts):
        raise SnapshotError(f"Snapshot path is not safe: {path}")
    return normalized


def _remove_created_file(root: Path, destination: Path) -> None:
    parent = destination.parent.resolve(strict=True)
    _assert_inside(parent, root)
    if destination.is_symlink():
        destination.unlink()
        return
    if not destination.exists():
        return
    resolved_destination = destination.resolve(strict=True)
    _assert_inside(resolved_destination, root)
    if not resolved_destination.is_file():
        raise SnapshotError(f"Refusing to remove non-file path: {destination}")
    resolved_destination.unlink()


def _restore_file(root: Path, destination: Path, snapshot_source: Path) -> None:
    parent = destination.parent
    if parent.exists() or parent.is_symlink():
        resolved_parent = parent.resolve(strict=True)
        _assert_inside(resolved_parent, root)
        if not resolved_parent.is_dir():
            raise SnapshotError(f"Restore parent is not a directory: {parent}")
    else:
        parent.mkdir(parents=True)
        _assert_inside(parent.resolve(strict=True), root)

    if destination.is_symlink():
        raise SnapshotError(f"Refusing to restore through symlink path: {destination}")
    if destination.exists() and destination.is_dir():
        raise SnapshotError(f"Refusing to restore over directory: {destination}")
    if destination.exists():
        _assert_inside(destination.resolve(strict=True), root)

    destination.write_bytes(snapshot_source.read_bytes())


def _assert_inside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SnapshotError(f"Path escapes project root: {path}") from exc
