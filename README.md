# agentic-sdlc (asdlc)

A local agentic SDLC platform that listens for git push webhooks and automatically runs LLM-powered analysis agents against every code change.

## What it does

Every `git push` to a connected repo triggers a workflow that dispatches one or more agents:

| Agent | Responsibility |
|---|---|
| Code Reviewer | Bugs, logic errors, best practices, missing error handling |
| Security Analyst | OWASP Top 10, exposed secrets, weak crypto, injection risks |
| Performance Analyst | Algorithmic inefficiency, N+1 queries, memory leaks, blocking I/O |
| Dependency Auditor | Checks added/changed packages against OSV.dev for known CVEs (no LLM needed) |
| Test Coverage Checker | Flags modified Python files with no corresponding test file |

Agents run sequentially or in parallel depending on the workflow:

- **Sequential** (full_review, quick_review) — agents run one at a time; each receives prior agent findings as additional context
- **Parallel** (security_focus) — agents run concurrently with independent analysis

Agents can also delegate to each other as sub-agents (used in quick_review to escalate concerns).

## Workflow routing

The router picks a workflow automatically based on the push:

| Trigger | Workflow |
|---|---|
| Push to `main`, `master`, or `release/*` | Full review — all five agents, sequential |
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

## API

### Webhook (push ingestion)

The webhook returns immediately with a 202 (Accepted) response while the analysis runs in the background:

```json
{
  "status": "accepted",
  "run_id": "a3f1c2d4-...",
  "workflow": "full_review",
  "repo": "my-app",
  "branch": "main",
  "commits": 3
}
```

Each finding has:
- `title`, `description`, `recommendation` — actionable guidance
- `severity` — one of: `critical`, `high`, `medium`, `low`, `info`
- `file_path`, `line_number` — optional location hints

### Results API

Findings are persisted to SQLite and queryable via REST:

```
GET /results                       list recent runs (optional: ?repo=&branch=&limit=)
GET /results/{run_id}              full WorkflowResult JSON for a specific run
```

### Dashboard

```
GET /                              HTML dashboard — recent runs with findings summary
```

### Observability

```
GET /healthz                       always 200 — liveness probe
GET /readyz                        200 if Ollama is reachable, 503 otherwise
GET /metrics                       Prometheus text — findings_total, agent_duration, timeouts, errors
```

### Replay CLI

Replay a saved push event JSON against the server and poll for results:

```sh
asdlc-replay push_event.json
asdlc-replay push_event.json --url http://localhost:9090/git/push --secret mysecret
```

## Setup

### Docker (recommended)

**Requirements:** [Docker](https://docs.docker.com/get-docker/) with the Compose plugin.

```sh
# Start the server and Ollama sidecar
docker compose up --build

# Pull the model (first time only — in a separate terminal)
docker compose exec ollama ollama pull qwen2.5-coder:7b
```

The server is now live at **http://localhost:9090**.

On subsequent runs `--build` is optional unless you changed code:

```sh
docker compose up
```

**Mounting local repos** — git tools read repos from the container filesystem. Set `REPOS_ROOT` to the directory on your host that contains your cloned repos:

```sh
REPOS_ROOT=/path/to/your/repos docker compose up
```

Repos are mounted read-only at `/repos` inside the container. Use `/repos/<repo-name>` as the `clone_url` in webhook payloads.

---

### Local (without Docker)

**Requirements:** Python 3.11+, [Ollama](https://ollama.com) running locally with `qwen2.5-coder:7b` pulled.

```sh
ollama pull qwen2.5-coder:7b

python -m venv .venv && source .venv/bin/activate
pip install -e .

uvicorn main:app --port 9090
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

WEBHOOK_URL="${GIT_WEBHOOK_URL:-http://localhost:9090/git/push}"
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

Test the hook by making a commit and pushing:

```sh
git commit --allow-empty -m "test push"
git push  # triggers the webhook to POST the payload
```

Watch the asdlc server logs to see the analysis run.

## Test repo

[inventory-tracker](../inventory-tracker) is a dummy repo wired up to this server for testing. It has the hook installed and a bare remote at `../inventory-tracker.git`.

## Configuration

All settings can be overridden via environment variables or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Default model for all agents |
| `CODE_REVIEW_MODEL` | _(OLLAMA_MODEL)_ | Override model for code reviewer |
| `SECURITY_MODEL` | _(OLLAMA_MODEL)_ | Override model for security analyst |
| `PERFORMANCE_MODEL` | _(OLLAMA_MODEL)_ | Override model for performance analyst |
| `DEP_AUDIT_MODEL` | _(OLLAMA_MODEL)_ | Override model for dependency auditor |
| `TEST_COVERAGE_MODEL` | _(OLLAMA_MODEL)_ | Override model for test coverage checker |
| `AGENT_TIMEOUT_SECONDS` | `180` | Per-agent timeout |
| `MAX_TOKENS` | `4096` | Max tokens per LLM response |
| `MAX_CONCURRENT_RUNS` | `3` | Max simultaneous workflow runs |
| `WEBHOOK_SECRET` | _(empty)_ | HMAC secret — if set, all requests must be signed |
| `RESULT_WEBHOOK_URL` | _(empty)_ | POST WorkflowResult JSON here after each run |
| `DB_PATH` | `asdlc.db` | SQLite database path |
| `API_KEY` | _(empty)_ | Bearer token required on `/results`, `/metrics` — if unset, no auth |
| `LOG_LEVEL` | `INFO` | Python logging level |

## Running tests

```sh
pytest tests/
```

The test suite covers HMAC signature validation (7 cases).

## Understanding Agent Delegation

In the `quick_review` workflow, the code reviewer can delegate to security and performance specialists if needed:

```
CodeReviewAgent (initial analysis)
  ├── Detects security concern → delegates to SecurityAnalystAgent
  ├── Detects performance issue → delegates to PerformanceAnalystAgent
  └── Returns aggregated findings from all three
```

Delegation happens via tool calling within the agent's agentic loop. The delegated agent operates on the same git diff but can use its specialized system prompt and tools.

## Agent Execution Status

Each agent reports its execution status:
- `success` — analysis completed normally
- `error` — uncaught exception during tool execution
- `timeout` — exceeded `AGENT_TIMEOUT_SECONDS` (default 180s)

Timeout or error status prevents that agent from contributing findings to the workflow result.

## Reading the Logs

When a push is received, you'll see output like:

```
INFO main push parsed OK repo=my-app branch=main pusher=Alice commits=2
INFO main routed repo=my-app branch=main → workflow=full_review (rule=main_branch)
INFO main workflow=full_review repo=my-app branch=main mode=sequential agents=3
INFO code_reviewer === [code_reviewer] SYSTEM PROMPT ===
      (system prompt here)
INFO code_reviewer === [code_reviewer] USER INPUT ===
      (repository context, diff, etc.)
INFO code_reviewer === [code_reviewer] TURN 1 ===
      (LLM request/response details)
INFO code_reviewer Agent turn 1 tool calls: ['fetch_git_diff']
INFO code_reviewer result: 0 findings
INFO main workflow=full_review done duration=45.2s total_findings=3
INFO main
[FULL REVIEW SUMMARY]
...
```

Key log lines:
- `routed → workflow=X` — which workflow was picked and why
- `TURN N` — which agentic loop iteration
- `tool calls` — which tools the agent used
- `result: N findings` — findings parsed from that agent
- `total_findings=N` — aggregate count across all agents in the workflow

## Git Tools Available to Agents

All agents have access to these tools during analysis:

| Tool | Purpose |
|---|---|
| `fetch_git_diff` | Get unified diff between two commits (capped at 30KB) |
| `get_file_content` | View a specific file at a given ref (capped at 20KB) |
| `list_changed_files` | List files with A (Added), M (Modified), D (Deleted) status |
| `delegate_to_agent` | Call another agent for specialized analysis (quick_review only) |

Git tools work on local filesystem paths. In sequential workflows, agents can examine the full diff early on, then request specific files for deeper analysis.

## Known limitations

- **Context truncation** — in sequential mode, prior-agent findings are passed as context capped at 2000 chars. Long findings JSON can be truncated mid-object, causing subsequent agents to miss prior context.
- **Findings parsing** — agents are prompted to emit `---FINDINGS---` before JSON, but the model sometimes emits bare blocks or tool-call-shaped responses, which results in 0 parsed findings for that agent.
- **Local repos only** — git tools resolve paths on the local filesystem; remote URLs are not cloned.
- **Timeout applies per agent** — long-running agents (especially in sequential workflows) may timeout at 180s, preventing downstream agents from running.
