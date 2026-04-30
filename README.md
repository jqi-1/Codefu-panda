# Codefu Panda

<p align="center">
  <img src="Codefu-Panda.png" alt="Codefu Panda" width="360">
</p>

A local, safety-first coding agent CLI prototype for inspecting a project,
showing suggestions, proposing commands, and proposing file edits. The agent
keeps enforcement local: commands and edits require explicit confirmation before
anything is executed or changed.

## Requirements

- Python 3.10+
- Optional: LM Studio running an OpenAI-compatible local API server

This project currently uses only the Python standard library. See
`requirements.txt` for the dependency note.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

Run the CLI against a project directory:

```powershell
python -m local_agent .
```

or:

```powershell
python -m local_agent.main .
```

On startup, the agent scans the project, prints a summary, displays exactly two
suggestions, and opens an interactive menu:

```text
Type suggest, run, edit, or quit:
```

Every command or edit proposal must be approved with exactly `yes` or `no`.
Invalid confirmation input is rejected and logged.

## Configuration

The agent reads optional settings from `.agent_config.json` in the target project
root:

```json
{
  "ai_endpoint": "http://localhost:1234/v1/chat/completions",
  "ai_model": "your-loaded-model-id",
  "command_timeout": 30
}
```

Additional supported keys:

- `additional_allowed_commands`: list of command names to add to the default
  command allowlist
- `additional_ignored_directories`: list of directory names to skip while
  scanning

For LM Studio setup details, see `instructions.md`.

## Safety Model

- Commands must be single-line commands.
- Shell metacharacters are blocked.
- Destructive raw patterns are blocked.
- Command execution is limited to an allowlist.
- File edits must stay inside the project root.
- All actions, denials, errors, and command output are logged to
  `agent_history.md`.

## Tests

Run the test suite with:

```powershell
python -m unittest discover
```
