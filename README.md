# Codefu Panda

<p align="center">
  <img src="Codefu-Panda.png" alt="Codefu Panda" width="360">
</p>

A local, safety-first coding agent CLI prototype for inspecting a project,
showing suggestions, proposing commands, and proposing file edits. The agent
keeps enforcement local: commands and edits require explicit confirmation before
anything is executed or changed.

## What it does

Codefu Panda scans a project directory, summarizes likely languages and entry
points, asks what kind of help you want, and then offers an interactive menu:

```text
Type suggest, run, edit, or quit:
```

- `suggest` asks the advisory AI for exactly two project suggestions.
- `run` asks for command proposals, lets you choose one, validates it locally,
  and only runs it after explicit approval.
- `edit` asks for unified-diff edit proposals for one target file, then applies
  only a selected and approved diff.

The AI backend only proposes text. Validation, approval, command execution, and
file edits are handled locally by deterministic code.

## Safety model

- Commands are validated before execution.
- Commands run with `shell=False`.
- Shell metacharacters and command chaining constructs are blocked.
- Dangerous commands and destructive raw patterns are blocked.
- Risky commands, such as dependency installs or project-defined scripts, require
  explicit `yes` confirmation.
- The selected command is validated again immediately before execution.
- File edits must be unified diffs.
- File edits are sandboxed to the project root.
- The AI connector cannot execute commands or apply edits directly.
- `agent_history.md` is ignored by Git because it may contain command output,
  prompts, diffs, file paths, errors, and other sensitive information. Do not
  commit it accidentally.

## Quick start

Requirements:

- Python 3.10+
- Optional: LM Studio running an OpenAI-compatible local API server

Create an environment and install the CLI in editable mode:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Run against the current project:

```powershell
python -m local_agent .
```

or, after editable install:

```powershell
codefu-panda .
```

The legacy module entry point also remains available:

```powershell
python -m local_agent.main .
```

## LM Studio setup

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

The CLI still fails closed if LM Studio is unavailable or returns invalid
structured output. Suggestions fall back to deterministic local suggestions;
command and edit proposals are not executed or applied.

More detailed LM Studio notes are in `instructions.md`.

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

## Usage examples

Run with the package module:

```powershell
python -m local_agent .
```

Run with the main module:

```powershell
python -m local_agent.main .
```

Run with the console command after editable install:

```powershell
codefu-panda .
```

Run the test suite:

```powershell
python -m unittest discover
```

Run a syntax compile check:

```powershell
python -m compileall local_agent tests
```

## Development

Keep changes small and safety-first. The project should remain dependency-light,
compatible with Python 3.10+, and usable as a local CLI.

Useful checks:

```powershell
python -m compileall local_agent tests
python -m unittest discover
```

Do not change the AI connector to execute commands or apply edits. Command
execution must continue through the permission manager and command runner, and
edits must continue through unified-diff validation.

## Threat model

Codefu Panda assumes AI output is untrusted. The advisory backend may suggest
commands, paths, or diffs that are unsafe, malformed, or unrelated to the user's
intent, so local validation is the enforcement boundary.

Command protections:

- Commands are parsed with `shlex` and executed as token lists with
  `shell=False`.
- Shell metacharacters such as pipes, redirects, command separators, command
  substitution, and backticks are blocked.
- Commands outside the allowlist are blocked.
- Dangerous commands and destructive Git operations are blocked.
- Working-tree-mutating or history-mutating Git commands are blocked in v0.
- Path arguments must stay inside the project root, including when supplied
  after path-valued options.
- Symlink escapes are rejected.

Edit protections:

- Edits must be unified diffs.
- Only one file may be edited per diff in v0.
- File deletion, renames, binary edits, and paths outside the project root are
  rejected.
- The user must explicitly approve the selected diff before it is applied.

Logging note:

`agent_history.md` is append-only local history for startup events, prompts,
denials, approvals, command output, diffs, and errors. Treat it as sensitive.
It is ignored by Git, but users should still avoid copying or committing it.
