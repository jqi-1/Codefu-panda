# Architecture

## Goals

- Local-first coding assistant
- Safety-first command and edit validation
- AI backend is advisory only
- Deterministic local enforcement

## Trust boundaries

The user owns the local repository. Codefu Panda treats AI output as untrusted
text and never lets the AI connector execute commands or apply edits directly.

The AI connector proposes text only. The strict model protocol parser validates
the JSON shape before any proposal reaches local enforcement. The permission
manager validates commands, the diff validator validates edits, and the executor
applies only approved validated actions inside the project root.

## Command flow

1. User asks for action.
2. Model proposes a strict JSON command object.
3. Parser validates the JSON schema.
4. Permission manager validates the command.
5. User confirms if needed.
6. Executor runs the command with `shell=False`.
7. Logger records the result.

## Edit flow

1. Model proposes a strict JSON edit diff.
2. Parser validates the JSON schema.
3. Diff validator validates the unified diff.
4. Snapshot is created.
5. Patch is applied.
6. User can restore the snapshot.

## Dry-Run Flow

Dry-run mode runs the same local validation as a real command or edit request,
then skips execution and file modification. Dry-run command proposals report
whether they would be allowed, risky, or blocked. Dry-run edit proposals report
the file that would be modified and do not create snapshots.

## Snapshot and Restore

Real edit operations create snapshots in
`.codefu-panda/snapshots/<timestamp>/` before modifying files. Each snapshot has
a `metadata.json` file that records project-relative paths, stored snapshot
files, and whether the original file was missing.

Restore uses the most recent snapshot by default, or a specific snapshot id when
provided. Existing files are overwritten with snapshotted content. Files created
by an edit are removed when metadata records `original_missing = true`.

## Failure Behavior

Codefu Panda fails closed when model output is malformed, uses Markdown fences,
has extra fields, or does not match a supported proposal type. Commands with
shell syntax, blocked destructive patterns, dangerous Git operations, or paths
outside the project root are rejected. Edits with blocked paths, file deletion,
renames, multiple files, binary files, or mismatched diff context are rejected.
The CLI does not automatically install dependencies.

## Non-Goals for v0.2

- No autonomous background agent
- No automatic Git commits or pushes
- No multi-repo operations
- No `shell=True`
- No unreviewed dependency installation
