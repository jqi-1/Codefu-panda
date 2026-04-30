# LM Studio Setup Instructions

This project can use LM Studio as the advisory AI backend for suggestions, command proposals, and edit proposals. The CLI still enforces all safety rules locally; LM Studio only proposes text.

## 1. Start LM Studio

1. Install and open LM Studio.
2. Download a chat or instruct model.
3. Start the local API server on port `1234`.

From the LM Studio app, use the Developer/API server controls.

If the `lms` CLI is available, you can also run:

```powershell
lms server start --port 1234
```

## 2. Confirm the Server Is Reachable

Run this from PowerShell:

```powershell
Invoke-RestMethod http://localhost:1234/v1/models | ConvertTo-Json -Depth 6
```

The response should list one or more available model ids. Copy the model id you want the agent to use.

If this times out or fails, confirm that LM Studio is open, the server is running, and the port is `1234`.

## 3. Configure This CLI

Create `.agent_config.json` in the project root:

```json
{
  "ai_endpoint": "http://localhost:1234/v1/chat/completions",
  "ai_model": "your-loaded-model-id",
  "command_timeout": 30
}
```

Replace `your-loaded-model-id` with the id returned by `/v1/models`.

Leave LM Studio API authentication disabled for v0. The current connector does not send an `Authorization` header.

## 4. Run the Agent

From this repository:

```powershell
python -m local_agent.main .
```

The CLI will scan the project, append to `agent_history.md`, print a project summary, show exactly two suggestions, and enter the interactive menu.

## 5. Verify the Wiring

The setup is working when:

- `http://localhost:1234/v1/models` returns model data.
- `python -m local_agent.main .` starts without backend connection errors.
- `agent_history.md` does not show `Using deterministic suggestion fallback`.
- Choosing `run` or `edit` asks LM Studio for one structured proposal instead of failing closed.

If LM Studio is unavailable or returns invalid output, the CLI intentionally fails closed:

- `suggest` uses deterministic fallback suggestions.
- `run` and `edit` do not propose or execute anything.
- The failure is logged to `agent_history.md`.

## 6. Useful References

- LM Studio REST quickstart: https://lmstudio.ai/docs/developer/rest/quickstart
- LM Studio server CLI: https://lmstudio.ai/docs/cli/serve/server-start
- OpenAI-compatible endpoints: https://lmstudio.ai/docs/app/api/endpoints/openai
