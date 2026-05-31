# agentic-sdlc (asdlc)

A local agentic SDLC platform that listens for git push webhooks and automatically runs LLM-powered analysis agents against every code change.

## What it does

Every `git push` to a connected repo triggers a workflow that dispatches one or more agents:

| Agent | Responsibility |
|---|---|
| Code Reviewer | Bugs, logic errors, best practices, missing error handling |
| Security Analyst | OWASP Top 10, exposed secrets, weak crypto, injection risks |
| Performance Analyst | Algorithmic inefficiency, N+1 queries, memory leaks, blocking I/O |

Agents run sequentially or in parallel depending on the workflow. In sequential mode each agent receives the findings of the previous one as context. Agents can also delegate to each other as sub-agents.

## Workflow routing

The router picks a workflow automatically based on the push:

| Trigger | Workflow |
|---|---|
| Push to `main`, `master`, or `release/*` | Full review — all three agents, sequential |
| Changed files match secrets/auth/cert patterns | Security focus — security + performance, parallel |
| Everything else | Quick review — code reviewer only, can sub-delegate |

Sensitive file patterns: `.env`, `.pem`, `.key`, `.cert`, `.p12`, `.pfx`, or any path containing `secret`, `credential`, `password`, `token`, `auth`, `oauth`, or `jwt`.

## Architecture

```
POST /git/push
    └── verify_webhook_signature    HMAC-SHA256 (optional)
    └── WorkflowRouter              picks workflow by branch / changed files
    └── WorkflowOrchestrator        fetches diff, runs agents, aggregates results
            └── BaseAgent           Ollama agentic loop (OpenAI-compatible tool calling)
                    ├── fetch_git_diff      git diff between two SHAs
                    ├── get_file_content    file content at a given ref
                    ├── list_changed_files  changed file list with A/M/D status
                    └── delegate_to_agent   sub-agent delegation (quick_review only)
```

Results are logged to stdout. There is no persistence layer — findings are not stored.

## Setup

**Requirements:** Python 3.11+, [Ollama](https://ollama.com) running locally with `qwen2.5-coder:7b` pulled.

```sh
ollama pull qwen2.5-coder:7b

python -m venv .venv && source .venv/bin/activate
pip install -e .

uvicorn main:app --port 8080
```

**Optional — HMAC signature validation:**

```sh
# Generate a secret
openssl rand -hex 32

# Add to .env
echo "WEBHOOK_SECRET=<your-secret>" >> .env

# Set the same value on the pushing repo side (read by the pre-push hook)
export GIT_WEBHOOK_SECRET=<your-secret>
```

## Install the push hook on a repo

Write a `.git/hooks/pre-push` script that POSTs a GitHub-compatible push payload to the server on every push:

```sh
#!/usr/bin/env bash
set -euo pipefail

WEBHOOK_URL="${GIT_WEBHOOK_URL:-http://localhost:8080/git/push}"
REPO_NAME=$(basename "$(git rev-parse --show-toplevel)")
REMOTE_URL=$(git remote get-url "${1:-origin}" 2>/dev/null || echo "")
PUSHER_NAME=$(git config user.name)
PUSHER_EMAIL=$(git config user.email)

while read local_ref local_sha remote_ref remote_sha; do
    branch="${remote_ref#refs/heads/}"
    payload=$(jq -nc \
        --arg ref "refs/heads/$branch" \
        --arg before "$remote_sha" \
        --arg after "$local_sha" \
        --arg name "$REPO_NAME" \
        --arg clone_url "$REMOTE_URL" \
        --arg pusher_name "$PUSHER_NAME" \
        --arg pusher_email "$PUSHER_EMAIL" \
        '{ref: $ref, before: $before, after: $after,
          repository: {name: $name, clone_url: $clone_url},
          pusher: {name: $pusher_name, email: $pusher_email},
          commits: []}')

    if [[ -n "${GIT_WEBHOOK_SECRET:-}" ]]; then
        sig="sha256=$(echo -n "$payload" | openssl dgst -sha256 -hmac "$GIT_WEBHOOK_SECRET" | awk '{print $2}')"
        curl -s -o /dev/null -X POST "$WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -H "X-Hub-Signature-256: $sig" \
            -d "$payload"
    else
        curl -s -o /dev/null -X POST "$WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "$payload"
    fi
done
```

```sh
chmod +x /path/to/your/repo/.git/hooks/pre-push
```

## Test repo

[inventory-tracker](../inventory-tracker) is a dummy repo wired up to this server for testing. It has the hook installed and a bare remote at `../inventory-tracker.git`.

## Configuration

All settings can be overridden via environment variables or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model to use for all agents |
| `WEBHOOK_SECRET` | _(empty)_ | HMAC secret — if set, all requests must be signed |
| `AGENT_TIMEOUT_SECONDS` | `180` | Per-agent timeout |
| `MAX_TOKENS` | `4096` | Max tokens per LLM response |
| `LOG_LEVEL` | `INFO` | Python logging level |

## Running tests

```sh
pytest tests/
```

The test suite covers HMAC signature validation (7 cases).

## Known limitations

- **Context truncation** — in sequential mode, prior-agent findings are passed as a string capped at 2000 chars. Long findings JSON can be truncated mid-object.
- **Findings parsing** — agents are prompted to emit `---FINDINGS---` before a JSON array, but the model sometimes emits bare JSON blocks or tool-call-shaped responses, which results in 0 parsed findings for that run.
- **No persistence** — results are logged only; there is no storage, dashboard, or notification integration.
- **Local repos only** — git tools resolve paths on the local filesystem; remote URLs are not cloned.
