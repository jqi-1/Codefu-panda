# Local Coding Agent CLI Prototype Specification

Build a local, safety-first coding assistant CLI for working inside a single project directory.

The agent must inspect a project, suggest improvements, propose commands, propose code edits, and apply only explicitly confirmed actions. The agent must never make unconfirmed changes.

---

## 1. Core Goal

Create a Python 3.10+ CLI prototype that operates on a user-provided project root.

The agent should:

1. Scan the project.
2. Print a structured project summary.
3. Display exactly two suggestions.
4. Enter an interactive command loop.
5. Require explicit confirmation before every command execution or file edit.
6. Log every action, decision, command output, denial, and error to `agent_history.md`.

The system must prioritize safety, transparency, and deterministic enforcement over convenience.

---

## 2. User Interaction Rules

### 2.1 Confirmation Format

Whenever the agent asks for permission, the user must reply with exactly one of:

```text
yes
no
```

The check is case-insensitive, so `YES`, `Yes`, and `yes` are equivalent.

Before validation, the agent must apply `strip()` to remove leading and trailing whitespace, including the newline sent by the terminal. After trimming, the remaining text must be exactly `yes` or `no`, case-insensitively.

Any other input is invalid.

If the user replies with anything other than `yes` or `no`, the agent must:

Print:

```text
Please answer exactly yes or no.
```

Ask the same confirmation question again.

Log the invalid response as `INVALID_CONFIRMATION`.

There is no time-based auto-approval.

### 2.2 Command Execution Consent

Before executing any command, the agent must display the exact command and ask:

```text
Run this command? (yes/no)
```

On `yes`:

1. Validate the command.
2. Execute the command safely.
3. Use a default timeout of 30 seconds.
4. Capture stdout, stderr, and exit code.
5. Display stdout and stderr.
6. Log the full command, user decision, stdout, stderr, exit code, and timeout status.

On `no`:

1. Do not execute anything.
2. Log the denial as `COMMAND_DENIED`.

The agent must not execute any command without confirmation.

### 2.3 Allowed Commands

The agent may only execute commands whose first token is in the configured whitelist.

The first token means the first item produced by `shlex.split(command_string)` after the raw command string has passed shell-safety validation.

Commands such as `sudo pytest`, `bash -c ...`, or `cmd /c ...` are blocked by default because their first token is not in the whitelist.

Default whitelist:

```text
git
npm
npx
yarn
pnpm
python
pytest
flake8
eslint
prettier
mypy
black
isort
go
cargo
make
```

Any command whose first token is not in the whitelist must be rejected.

When rejected, the agent must:

1. Print a clear error message.
2. Log the event as `COMMAND_BLOCKED`.
3. Not ask for execution confirmation.
4. Not execute anything.

The whitelist may be extended only through user configuration.

### 2.4 Command Parsing and Shell Safety

All commands must be parsed using `shlex.split`.

All commands must be executed with:

```python
shell=False
```

The agent must never use:

```python
shell=True
```

The agent must execute commands using the project root as the working directory.

The agent must not execute command strings through a shell.

The following shell metacharacters and shell constructs are forbidden in command input:

```text
>
>>
<
<<
|
&
;
&&
||
$()
`
```

The permission manager must scan the raw, unsplit command string before calling `shlex.split`. If any forbidden shell metacharacter or construct appears anywhere in the raw proposed command string, the command must be blocked, even if the character appears inside quotes and even if the command base is otherwise allowed.

After raw-string validation passes, the command must be parsed with `shlex.split`. If parsing fails, the command must be blocked and logged as `COMMAND_BLOCKED`.

When blocked, the agent must:

1. Print a clear `Blocked` message.
2. Log the event as `COMMAND_BLOCKED`.
3. Not execute anything.

### 2.5 Forbidden Command Patterns

The agent must block commands containing any of the following patterns:

```text
rm -rf
git push --force
:(){:|:&};
```

Forbidden pattern detection must be applied to the raw command string before execution.

These commands must be blocked even if the command base is otherwise allowed.

When blocked, the agent must:

1. Print a clear `Blocked` message.
2. Log the event as `COMMAND_BLOCKED` or `BLOCKED_DESTRUCTIVE_ACTION`, whichever is more specific.
3. Not execute anything.

### 2.6 Command Path Validation

The agent must restrict all command-related filesystem access to the project root as much as practical.

The project root must be resolved once at startup to an absolute real path.

For command arguments:

1. Any argument that resolves to an existing filesystem path must resolve inside the project root after following symlinks.
2. Any argument that appears to be a filesystem path but does not yet exist must be normalized against the project root if relative, or treated as absolute if absolute. Its normalized absolute path must still remain inside the project root.
3. Any symlink argument must be resolved before approval. If the symlink resolves outside the project root, the command must be blocked.
4. Any symlink whose target cannot be resolved safely must be blocked.
5. Non-path arguments, such as `-m`, `--help`, `--version`, or commit messages, do not need path validation unless they are associated with an option known to accept a path.

Examples of paths that must be denied:

```text
../other_project/file.py
/tmp/something.py
C:\Users\someone\other_project
../../secrets.txt
```

If a path argument points or would point outside the project root, the command must be blocked.

When blocked, the agent must:

1. Print a clear error message.
2. Log the event as `COMMAND_BLOCKED` or `SANDBOX_DENIED`, whichever is more specific.
3. Not execute anything.

Commands must run with the project root as the current working directory.

### 2.7 Potentially Risky Allowed Commands

Some allowed command bases may still perform destructive or broad actions.

Examples:

```text
npm install
pnpm add
yarn add
pip install
python -m pip install
cargo install
make clean
```

These commands are not automatically forbidden, but they require an additional warning in the confirmation prompt.

The agent must display:

```text
Warning: this command may modify dependencies, generated files, or the project environment.
Run this command? (yes/no)
```

The command still must pass all normal whitelist, path, shell, and forbidden-pattern validation.

Global package installation commands must be blocked as destructive or environment-modifying actions. Examples include `npm install -g`, `yarn global add`, `pnpm add -g`, `pip install --user` when it targets a user-level environment, and `cargo install` when it installs outside the project.

### 2.8 Controlled Code Writing and Editing

When suggesting an edit, the agent must:

1. Display a unified diff exactly as it would be applied.
2. Ask:

```text
Apply this edit? (yes/no)
```

On `yes`:

1. Validate all affected file paths.
2. Verify that the current file content matches the diff context.
3. Apply the edit atomically.
4. Log the full diff and the user’s decision.
5. Log whether the edit succeeded or failed.

On `no`:

1. Do nothing.
2. Log the denial as `EDIT_DENIED`.

The agent must never create, overwrite, truncate, or modify a file without confirmation, except for the required logging file described below.

### 2.9 File Edit Rules

File edits must be applied from unified diffs only.

Every affected path in a diff must resolve inside the project root after following symlinks.

Before applying a diff, the agent must verify that the file content still matches the expected diff context.

Diff context matching must be deterministic:

1. The agent should preserve the file’s existing newline style when practical.
2. If CRLF/LF differences prevent exact context matching, the edit must be treated as a conflict unless the implementation explicitly normalizes both the diff and file content in a deterministic way before matching.
3. If the file has changed between proposal and application, and the context no longer matches exactly, the edit must be aborted.

If the context does not match, the agent must:

1. Abort the edit.
2. Print a conflict message.
3. Log the event as `CONFLICT`.

File edits must be applied atomically:

1. Write the new content to a temporary file in the same directory as the target file.
2. Preserve original file permissions when practical.
3. Flush the temporary file.
4. Call `fsync` on the temporary file when practical.
5. Replace the original file using `os.replace`.
6. Log the temporary path, target path, and whether `os.replace` succeeded.

If `os.replace` fails, including on Windows when the target file is locked or open elsewhere, the agent must:

1. Print a clear error message.
2. Leave the original file unchanged when possible.
3. Log the event as `EDIT_FAILED` with the error message.

New file creation is allowed only after explicit edit confirmation.

File deletion is forbidden in v0.

Directory deletion is forbidden in v0.

Binary file editing is forbidden in v0.

### 2.10 Logging File Exception

The agent may create or append to:

```text
agent_history.md
```

inside the project root without confirmation because logging is required by this specification.

The agent must never overwrite, truncate, delete, or move `agent_history.md`.

The agent may only append to it.

Before opening `agent_history.md`, the logger must resolve its real path and verify that it is inside the project root. If `agent_history.md` is a symlink that resolves outside the project root, logging must fail safely, the agent must print a clear error, and the agent must not continue performing actions that cannot be logged.

No log rotation, truncation, compaction, or size-limit behavior is allowed in v0.

---

## 3. Permission and Safety Model

### 3.1 Gate Kinds

The agent has exactly three action categories:

1. Read-only actions
2. Command execution
3. File edits

No other action category exists in v0.

### 3.2 Read-Only Actions

Read-only actions include:

```text
file listing
directory scanning
reading text files
counting files
detecting project language
detecting likely entry points
```

Read-only actions are automatically allowed.

Read-only file access must still be sandboxed to the project root.

The scan itself may be logged as a summarized `PROJECT_SUMMARY` entry rather than logging one `READ` entry for every file encountered. Explicit file reads used to inspect file contents for suggestions, commands, or edits must be logged as `READ` entries with the affected path.

Text reading must be conservative:

1. Prefer UTF-8.
2. If a file cannot be decoded as text, skip it and log the skip as `READ` or `ERROR` depending on severity.
3. Files that appear binary must not be read as text and must not be edited.

Large projects must still respect the ignore list. The scanner must not recurse into ignored directories.

### 3.3 Command Execution

Command execution requires per-command confirmation.

The agent must:

1. Propose one command.
2. Display the exact command.
3. Ask for confirmation.
4. Validate the command.
5. Execute only if the user confirmed and validation passed.
6. Log the full result.

The agent must not batch multiple commands under one confirmation.

Each command requires its own confirmation.

If a proposed command is blocked by validation, the agent must log the block and return to the menu. It must not automatically propose a replacement command in the same menu cycle.

### 3.4 File Edits

File edits require per-edit confirmation.

The agent must:

1. Propose one unified diff.
2. Display the full diff.
3. Ask for confirmation.
4. Validate the diff.
5. Apply only if the user confirmed and validation passed.
6. Log the full result.

The agent must not batch unrelated edits under one confirmation.

If a proposed edit is blocked by validation or fails due to conflict, the agent must log the outcome and return to the menu. It must not automatically propose a replacement edit in the same menu cycle.

### 3.5 Sandboxing

The project root is the only allowed filesystem boundary.

The agent must resolve the project root to an absolute real path at startup.

All file operations must remain inside this resolved project root.

The agent must resolve symlinks before allowing any read or write operation.

Any attempt to read or write outside the project root is an error.

When denied, the agent must:

1. Print a clear error message.
2. Log the event as `SANDBOX_DENIED`.
3. Do nothing.

### 3.6 Destructive Operations

The agent must never perform destructive operations in v0.

Destructive operations include:

```text
deleting files
deleting directories
truncating files
force-pushing Git history
changing Git remotes
modifying files outside the project root
installing global packages
changing system configuration
killing processes outside the current subprocess tree
editing binary files
overwriting files without diff confirmation
modifying `.env` files without explicit file-edit confirmation
```

If a proposed action appears destructive, the agent must block it and log the event as `BLOCKED_DESTRUCTIVE_ACTION`.

---

## 4. Architecture and Implementation Constraints

### 4.1 Language

Use:

```text
Python 3.10+
```

The code should be clear, modular, and self-documenting.

Prefer standard library modules when practical.

### 4.2 Required Modules

The prototype must include these modules:

```text
file_watcher
permission_manager
command_runner
ai_connector
logger
main
```

### 4.3 Module Responsibilities

#### file_watcher

Responsible for:

```text
scanning the project
respecting ignored directories
counting files
detecting languages
detecting likely entry points
reading allowed text files
```

It must not read outside the project root.

It must ignore common large or generated directories by default:

```text
.git
node_modules
.venv
venv
__pycache__
dist
build
target
.coverage
.mypy_cache
.pytest_cache
.next
.cache
```

User configuration may extend the ignore list.

Language detection should use conservative heuristics based on file extensions and dependency files.

Likely entry point detection should use conservative heuristics such as common filenames and project manifests, for example:

```text
main.py
app.py
src/main.py
package.json scripts
pyproject.toml
Cargo.toml
Makefile
```

Test detection should use conservative heuristics, including:

```text
test/
tests/
__tests__/
test_*.py
*_test.py
*.test.js
*.spec.js
*_test.go
```

Dependency file detection should list known project files, including when present:

```text
requirements.txt
pyproject.toml
poetry.lock
Pipfile
package.json
pnpm-lock.yaml
yarn.lock
package-lock.json
Cargo.toml
go.mod
Makefile
```

#### permission_manager

Responsible for:

```text
command whitelist enforcement
forbidden pattern detection
raw command shell metacharacter blocking
command parsing with shlex.split
path sandbox validation
confirmation prompts
yes/no input validation
diff path validation
destructive action blocking
```

The AI backend must not bypass `permission_manager`.

All proposed commands and edits must pass through deterministic validation.

#### command_runner

Responsible for:

```text
running subprocesses
using shell=False
setting cwd to project root
applying timeout
capturing stdout
capturing stderr
capturing exit code
returning structured command results
```

Default timeout:

```text
30 seconds
```

The command runner must return a structured result with at least:

```text
command
stdout
stderr
exit_code
timed_out
error_message
```

If a command times out, the agent must:

1. Kill the subprocess.
2. Capture any available stdout and stderr.
3. Print a timeout message.
4. Log the timeout as `COMMAND_TIMEOUT`.
5. Return to the menu without automatically retrying.

#### ai_connector

Responsible for abstracting the AI backend.

It must expose an interface similar to:

```python
generate(prompt: str) -> str
```

The initial implementation may use either:

```text
Hugging Face transformers pipeline
LM Studio local API
```

The connector must be designed so backends can be swapped later.

The connector must work fully offline after initial model download or local model setup.

The AI backend is advisory only.

It may propose suggestions, commands, and diffs, but it must never directly execute commands or modify files.

The prompt sent to the AI backend must include the current request type, such as `suggest`, `run`, or `edit`, plus the project summary and only approved read-only project context.

AI output must be converted into one of the following structured proposal types before use:

```text
SuggestionProposal
CommandProposal
EditProposal
```

A `SuggestionProposal` must contain exactly two suggestions.

A `CommandProposal` must contain exactly one single-line command string and no shell metacharacters.

An `EditProposal` must contain exactly one unified diff block.

If AI output cannot be parsed into the expected structured proposal type, the agent must log `ERROR`, print a clear message, and return to the menu. It must not execute commands or apply edits from unstructured output.

#### logger

Responsible for writing to:

```text
agent_history.md
```

The logger must append only.

It must never overwrite or truncate the log file.

Every important event must be logged.

Before writing, the logger must verify that the resolved log path remains inside the project root.

#### main

Responsible for:

```text
argument parsing
startup flow
project summary display
initial suggestions
interactive menu loop
coordinating modules
clean shutdown
```

---

## 5. Logging Requirements

### 5.1 Log File

The log file must be:

```text
agent_history.md
```

It must be created inside the project root if it does not exist.

It must be appended to if it already exists.

### 5.2 Required Log Events

The agent must log these event types when applicable:

```text
STARTUP
READ
PROJECT_SUMMARY
SUGGESTIONS
MENU_INPUT
INVALID_MENU_INPUT
PROPOSE_COMMAND
COMMAND_APPROVED
COMMAND_DENIED
COMMAND_BLOCKED
COMMAND_RESULT
COMMAND_TIMEOUT
PROPOSE_EDIT
EDIT_APPROVED
EDIT_DENIED
EDIT_APPLIED
EDIT_FAILED
CONFLICT
SANDBOX_DENIED
BLOCKED_DESTRUCTIVE_ACTION
INVALID_CONFIRMATION
ERROR
SHUTDOWN
```

### 5.3 Log Entry Format

Each log entry must include:

```text
timestamp in ISO 8601 format
event type
working directory
human-readable description
```

When applicable, it must also include:

```text
full command
full unified diff
user decision
stdout
stderr
exit code
timeout status
error message
affected file paths
temporary file path for atomic edits
```

Recommended Markdown format:

````markdown
## 2026-04-30T12:34:56 - COMMAND_RESULT

- Working directory: `/path/to/project`
- Command: `pytest`
- Exit code: `0`
- Timed out: `false`

### stdout

```text
...
```

### stderr

```text
...
```
````

The logger must preserve full command outputs.

If the process crashes mid-log-entry, recovery or repair is not required in v0.

---

## 6. AI Behavior Constraints

### 6.1 Advisory Role

The AI backend is advisory only.

It may generate:

```text
project observations
suggestions
proposed commands
proposed unified diffs
```

It must not directly:

```text
execute commands
read files outside approved read-only flow
write files
bypass validation
bypass confirmation
modify logs
```

All AI-generated commands and diffs must be validated by `permission_manager`.

The AI prompt should explicitly instruct the backend not to produce shell metacharacters, chained commands, destructive commands, or more than one command or edit at a time. The deterministic permission manager remains the authority even if the AI ignores these instructions.

### 6.2 Suggestions

Suggestions must be practical and based on the scanned project.

On startup, after the project summary, the agent must display a section titled:

```text
Suggestions
```

This section must contain exactly two numbered suggestions:

1. One code-quality suggestion.
2. One project-health suggestion.

Example:

```text
Suggestions

1. Code quality: Add type hints to the public functions in `src/main.py`.
2. Project health: Add a basic test command to the README.
```

The startup suggestions section must contain exactly two suggestions, no more and no fewer.

The `suggest` menu command must also display exactly two new suggestions.

For an empty project or a project with too little information, the agent must still display exactly two suggestions. The suggestions may be conservative placeholders, such as adding a README or creating an initial test file, but they must not imply facts not found in the scan.

### 6.3 Command Proposals

When the user selects `run`, the agent must propose exactly one command.

The command must be relevant to the project.

The agent must display:

```text
Proposed command:
<command>
```

Then ask:

```text
Run this command? (yes/no)
```

The agent must not propose multiple commands at once.

The proposed command must be a single line. Multi-line commands are invalid and must be blocked.

### 6.4 Edit Proposals

When the user selects `edit`, the agent must propose exactly one unified diff.

The diff must be relevant to the project.

The agent must display:

```text
Proposed edit:
```

Then display the full unified diff.

Then ask:

```text
Apply this edit? (yes/no)
```

The agent must not propose multiple unrelated edits at once.

---

## 7. CLI Behavior

### 7.1 Startup

When pointed at a project path, the CLI must:

1. Resolve the project root to an absolute real path.
2. Create or append to `agent_history.md`.
3. Log `STARTUP`.
4. Scan the project.
5. Print a structured project summary.
6. Log `PROJECT_SUMMARY`.
7. Display exactly two suggestions.
8. Log `SUGGESTIONS`.
9. Enter the interactive menu loop.

### 7.2 Project Summary

The project summary must include:

```text
project root
detected primary language
detected secondary languages, if any
likely entry points
file count
ignored directory count
presence of tests
presence of dependency files
```

If no language can be detected, the primary language must be shown as `Unknown`.

If no likely entry points are found, the summary must show `None detected`.

Example:

```text
Project Summary

Root: /path/to/project
Primary language: Python
Secondary languages: Markdown, YAML
Likely entry points:
- main.py
- app.py
File count: 42
Ignored directories: 3
Tests detected: yes
Dependency files:
- requirements.txt
- pyproject.toml
```

### 7.3 Interactive Menu

After startup, the agent must enter a loop.

The user can type exactly one of:

```text
suggest
run
edit
quit
```

Menu input should be processed with `strip()`. The accepted commands are lowercase. Inputs with different casing are invalid unless explicitly normalized by the implementation; if normalized, this behavior must be consistent and documented in code comments.

Behavior:

#### suggest

The agent must:

1. Generate exactly two new suggestions.
2. Display them.
3. Log `SUGGESTIONS`.
4. Return to the menu.

#### run

The agent must:

1. Propose exactly one command.
2. Log `PROPOSE_COMMAND`.
3. Ask for confirmation.
4. On approval, validate and execute.
5. On denial, do nothing.
6. Log the outcome.
7. Return to the menu.

#### edit

The agent must:

1. Propose exactly one unified diff.
2. Log `PROPOSE_EDIT`.
3. Ask for confirmation.
4. On approval, validate and apply atomically.
5. On denial, do nothing.
6. Log the outcome.
7. Return to the menu.

#### quit

The agent must:

1. Log `SHUTDOWN`.
2. Exit cleanly.

Any other menu input is invalid.

For invalid menu input, the agent must:

Print:

```text
Invalid option. Type suggest, run, edit, or quit.
```

Log `INVALID_MENU_INPUT`.

Return to the menu.

---

## 8. Configuration

The agent may support an optional configuration file.

Suggested name:

```text
.agent_config.json
```

The config may allow:

```text
additional allowed commands
additional ignored directories
AI backend selection
model path or local endpoint
command timeout override
```

Configuration must not disable core safety rules.

The following rules must not be configurable in v0:

```text
confirmation requirement
project-root sandbox
shell=False requirement
logging requirement
file deletion ban
directory deletion ban
outside-root access ban
raw command shell metacharacter blocking
forbidden destructive pattern blocking
```

If configuration is invalid, the agent must:

1. Print a clear error.
2. Log `ERROR`.
3. Continue with defaults when safe.

---

## 9. Delivery Checklist

The CLI prototype must satisfy all of the following:

- Accept a project path as input.
- Resolve the project root safely.
- Create or append to `agent_history.md`.
- Verify that `agent_history.md` resolves inside the project root.
- Log `STARTUP`.
- Scan the project while respecting ignored directories.
- Print a structured project summary.
- Display exactly two startup suggestions.
- Enter an interactive menu loop.
- Accept only `suggest`, `run`, `edit`, or `quit` as menu commands.
- On `suggest`, display exactly two new suggestions.
- On `run`, propose exactly one command.
- Require exact `yes`/`no` confirmation before command execution.
- Trim leading and trailing whitespace before confirmation validation.
- Validate every command before execution.
- Scan the raw command string for forbidden shell metacharacters before parsing.
- Parse commands with `shlex.split`.
- Execute commands only with `shell=False`.
- Enforce the command whitelist.
- Block forbidden shell metacharacters.
- Block forbidden command patterns.
- Block command path arguments outside the project root.
- Validate non-existent path-like arguments so they cannot point outside the project root.
- Block unsafe symlinks.
- Capture stdout, stderr, exit code, and timeout status.
- Log full command output.
- On command timeout, capture available output, log `COMMAND_TIMEOUT`, and return to the menu.
- On `edit`, propose exactly one unified diff.
- Require exact `yes`/`no` confirmation before applying edits.
- Validate every affected file path before editing.
- Verify diff context before applying.
- Treat unmatched context as `CONFLICT`.
- Apply edits atomically using a temporary file in the same directory and `os.replace`.
- Preserve file permissions when practical.
- Log edit temporary path and replacement result.
- Forbid file deletion.
- Forbid directory deletion.
- Forbid binary file editing.
- Enforce project-root sandboxing for reads and writes.
- Keep `agent_history.md` up to date with all decisions and outputs.
- Never perform destructive operations.
- Never perform unconfirmed command execution.
- Never perform unconfirmed file edits.
- Exit cleanly on `quit`.

---

## 10. Non-Goals for v0

Do not add these features in v0:

```text
background agents
automatic code modification
automatic dependency installation
network-based remote execution
Git commits
Git pushes
multi-project workspace editing
web browsing
daemon mode
scheduled tasks
parallel command execution
plugin system
GUI
TUI
voice interface
log rotation
log compaction
automatic replacement proposals after blocked commands or failed edits
```

---

## 11. Final Instruction

Use this specification as the exact blueprint.

Do not skip any required safety detail.

Do not add extra features beyond what is written.

The implementation must be simple, local, auditable, and conservative.

End of specification.
