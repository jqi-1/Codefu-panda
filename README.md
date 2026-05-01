# Codefu Panda

<p align="center">
  <img src="Codefu-Panda.png" alt="Codefu Panda" width="360">
</p>

A local, safety-first coding agent CLI prototype for inspecting a project,
showing suggestions, validating command proposals, and applying tightly scoped
unified-diff edits. The AI backend is advisory only; deterministic local code
enforces safety.

## What It Does

Codefu Panda scans a project directory and starts an interactive menu:

```text
Type suggest, run, edit, or quit:
```

- `suggest` asks for project suggestions.
- `run` asks for a command proposal, validates it locally, and runs it only
  after explicit approval.
- `edit` asks for a unified-diff proposal for one target file, snapshots the
  original file, and applies the selected edit only after approval.
- `summarize` prints a read-only repository summary without using AI.
- `restore` restores the most recent edit snapshot.

## Quick Start

Requirements:

- Python 3.10+
- Optional: LM Studio running an OpenAI-compatible local API server for
  AI-backed suggestions, command proposals, and edit proposals

Install the CLI in editable mode:

```powershell
python -m pip install -e .
```

Run against the current project:

```powershell
codefu-panda .
```

The module entry points are also available:

```powershell
python -m local_agent .
python -m local_agent.main .
```

## Common Commands

Print help:

```powershell
codefu-panda --help
```

Run the interactive assistant:

```powershell
codefu-panda .
```

Run the interactive assistant in dry-run mode:

```powershell
codefu-panda . --dry-run
```

Print a read-only repository summary:

```powershell
codefu-panda . summarize
```

Restore the most recent edit snapshot:

```powershell
codefu-panda . restore
```

Restore a specific snapshot:

```powershell
codefu-panda . restore 2026-04-30T12-34-56Z
```

## Safety Model

- Commands are validated before execution.
- Commands run with `shell=False`.
- Shell metacharacters and command chaining constructs are blocked.
- Dangerous commands and destructive raw patterns are blocked.
- `npx`, dependency changes, and project-defined scripts are treated as risky
  and require explicit review.
- Shell wrapper commands such as `bash`, `powershell`, and `sudo` cannot be made
  safe through local config.
- The selected command is validated again immediately before execution.
- File edits must be unified diffs.
- File edits are sandboxed to the project root.
- Real edits create snapshots in `.codefu-panda/snapshots/`.
- Dry-run mode validates commands and edits without executing or modifying
  files.
- The AI connector cannot execute commands or apply edits directly.
- `agent_history.md` and `.codefu-panda/` are ignored by Git because they may
  contain local paths, command output, diffs, and file contents.

More detail is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Strict Model Protocol

AI-backed command and edit proposals must be exactly one JSON object. Markdown
fences, prose before or after JSON, arrays, unknown types, missing fields, and
extra fields fail closed.

Supported proposal shapes include:

```json
{"type":"command","command":"python -m unittest"}
```

```json
{"type":"edit","diff":"--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"}
```

```json
{"type":"plan","steps":["Inspect test failures","Run the test suite"]}
```

## LM Studio Setup

1. Install and open LM Studio.
2. Download a chat or instruct model.
3. Start the local API server on port `1234`.
4. Check `http://localhost:1234/v1/models` and copy the model id.
5. Put that model id in `.agent_config.json`.

Example configuration:

```json
{
  "ai_endpoint": "http://localhost:1234/v1/chat/completions",
  "ai_model": "your-loaded-model-id",
  "command_timeout": 30
}
```

The CLI fails closed if LM Studio is unavailable or returns invalid structured
output. Suggestions fall back to deterministic local suggestions; command and
edit proposals are not executed or applied.

## Configuration

The agent reads optional local settings from `.agent_config.json` in the target
project root. The file is local-only and ignored by Git.

Supported keys:

- `ai_endpoint`: OpenAI-compatible chat completions endpoint.
- `ai_model`: model id loaded in LM Studio.
- `command_timeout`: positive integer timeout in seconds.
- `additional_allowed_commands`: command names to add to the default allowlist.
- `additional_ignored_directories`: directory names to skip while scanning.

The runtime uses only the Python standard library.

## Development

Useful checks:

```powershell
python -m compileall local_agent tests
python -m unittest discover
```

Do not change the AI connector to execute commands or apply edits. Command
execution must continue through the permission manager and command runner, and
edits must continue through unified-diff validation.

## Known Limitations

- The CLI is intentionally conservative and may block valid commands.
- AI-backed `run` and `edit` require a working LM Studio-compatible endpoint.
- The AI backend is advisory only; invalid or unsafe model output fails closed.
- v0.2 edits are still intentionally limited.
- Codefu Panda does not automatically commit, push, install dependencies, or run
  background tasks.
