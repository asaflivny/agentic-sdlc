# asdlc — Remaining Backlog

Items not yet implemented, ordered by priority. Pick up any row and it should be self-contained enough to start from.

---

## P1 — Observability

### Structured JSON log output
**File:** `main.py`, all agents  
**Why:** Plain-text `logging.info` lines are hard to aggregate in Loki, Datadog, or CloudWatch. JSON logs let you filter by `repo`, `run_id`, `severity`, etc. without regex.  
**What to do:**
- Add `structlog` (or replace `logging.basicConfig` with a custom JSON formatter)
- Each log line should be a JSON object with at minimum: `ts`, `level`, `logger`, `event`, plus any kwargs passed to the log call
- Key structured fields already present as kwargs in the existing log calls — just needs the formatter wired up

---

## P2 — Integrations

### GitHub PR comment posting
**File:** new `integrations/github.py`, wired into `main.py` `_run_workflow`  
**Why:** Instead of polling `/results`, teams want findings posted directly as a PR review comment the moment a push is analysed.  
**What to do:**
- Add `GITHUB_TOKEN` and `GITHUB_REPO` config vars
- The push payload may contain a PR number; alternatively, use GitHub API to find open PRs matching the branch (`GET /repos/{owner}/{repo}/pulls?head={branch}`)
- Post a markdown summary as a PR review comment (`POST /repos/{owner}/{repo}/issues/{pr}/comments`)
- Format: collapsible `<details>` block per agent, severity badge per finding
- Only post when there are findings; skip if no `GITHUB_TOKEN` is set

---

## P3 — Agent Quality

### Diff chunking for large changes
**File:** `workflows/orchestrator.py`, `tools/git_tools.py`  
**Why:** Diffs over 30 KB are silently truncated today (`output[:30000]` in `fetch_diff`). Large refactors or dependency bumps lose most of the context.  
**What to do:**
- In `orchestrator.py` `_fetch_diff`: if the raw diff exceeds a threshold (e.g. 25 KB), split it into overlapping chunks (e.g. 20 KB chunks with 2 KB overlap)
- Run the agent once per chunk, collecting findings from each run
- Merge findings lists and deduplicate (same key already used in `_deduplicate_findings`)
- Add `DIFF_CHUNK_SIZE_KB` config var (default 25, 0 = disable chunking)
- Log a warning when chunking kicks in: `"diff too large (N KB), chunking into M parts"`

---

## P4 — Workflow & Routing

### Per-repo `.asdlc.yml` workflow overrides
**File:** new `workflows/repo_config.py`, wired into `workflows/router.py`  
**Why:** The routing rules are global and hard-coded. Teams want to opt specific repos into a different workflow, disable certain agents, or add custom file-pattern rules without touching server code.  
**What to do:**
- Parse `.asdlc.yml` from the repo root at `HEAD` using `get_file_content` (already exists)
- Schema:
  ```yaml
  workflow: full_review          # override which workflow to use
  agents:
    exclude: [test_coverage]     # drop specific agents
  routing:
    - pattern: "*.sol"           # extra file-pattern rule
      workflow: security_focus
  ```
- Fall back to global routing if no `.asdlc.yml` or file is missing/malformed
- Cache parsed config per `(repo_url, after_sha)` for the lifetime of the run

### Incremental / changed-files-only mode
**File:** `agents/base.py`, `models/results.py`  
**Why:** Agents today see the whole diff. For repos with large diffs only one subsystem at a time tends to change — passing the full 30 KB diff to the security agent when only config files changed wastes tokens and degrades focus.  
**What to do:**
- Add a per-agent `file_filter: list[str]` field to `AgentSpec` (glob patterns)
- In `orchestrator._build_agents`, attach the filter to each agent
- In `agent.run`, pre-filter `context.git_diff` to only include hunks for matching files before building the initial message
- `security_analyst` could filter to `*.py`, `*.js`, `*.ts`, auth/crypto paths; `dep_auditor` already does this manually — unify the pattern

### Fan-out workflow (run multiple workflows in parallel)
**File:** `workflows/orchestrator.py`, `main.py`  
**Why:** A push to a sensitive-file branch today can only trigger one workflow. Teams want `quick_review` + `security_focus` to run simultaneously and get a merged result.  
**What to do:**
- Add `fan_out: list[str]` optional field to `WorkflowDefinition`
- In `orchestrator.run`, if `fan_out` is set, run each named workflow concurrently via `asyncio.gather`
- Merge `agent_results` lists from all workflows; run deduplication across the merged set
- Expose as a new routing rule type: `FanOutRule`

---

## P5 — Developer Experience

### Hook installer script
**File:** `replay.py` (extend) or new `cli.py`  
**Why:** The pre-push hook setup requires copy-pasting a multi-line bash script from the README. A one-command installer removes the friction.  
**What to do:**
- Add `asdlc install-hook <repo-path>` subcommand (extend the existing `replay.py` CLI with `argparse` subparsers, or create a new `cli.py`)
- Write `.git/hooks/pre-push` from a template string embedded in the script
- `chmod +x` the hook file
- Validate that the server is reachable at `GIT_WEBHOOK_URL` (or `--url`) before writing
- Print clear next steps: set `GIT_WEBHOOK_SECRET`, do a test push
- Register as `asdlc` console_script entry point alongside `asdlc-replay`

### Docker Compose setup
**File:** new `docker-compose.yml`, `Dockerfile`  
**Why:** The manual Ollama install + port wiring is the biggest setup friction for new users.  
**What to do:**
  ```yaml
  # docker-compose.yml sketch
  services:
    asdlc:
      build: .
      ports: ["8088:8088"]
      environment:
        OLLAMA_BASE_URL: http://ollama:11434/v1
      depends_on: [ollama]
    ollama:
      image: ollama/ollama
      volumes: [ollama_data:/root/.ollama]
      ports: ["11434:11434"]
  ```
- `Dockerfile`: multi-stage, `python:3.13-slim`, install deps, copy source, `CMD uvicorn main:app --host 0.0.0.0 --port 8088`
- Add a `docker-compose up` quick-start section to README

### Expanded test suite
**File:** `tests/`  
**Why:** Only 7 HMAC tests exist. Agents, orchestrator, and router have zero coverage.  
**What to do (pick any):**
- `tests/test_router.py` — test `BranchPatternRule`, `FilePatternRule`, `DefaultRule` routing decisions with fixture `PushEvent` objects
- `tests/test_orchestrator.py` — mock the Ollama client; verify sequential context enrichment, deduplication, timeout handling
- `tests/test_dep_auditor.py` — mock `httpx` OSV responses; verify CVE finding generation and package extraction from diff strings
- `tests/test_findings_parser.py` — parametrize `BaseAgent._parse_findings` with the full set of model output shapes (sentinel, fenced block, bare array, dict wrapper, malformed JSON)
- `tests/test_store.py` — use `tmp_path` fixture; verify save/list/get round-trip on a real SQLite file

---

## Effort summary

| Item | Effort | Risk |
|---|---|---|
| Structured JSON logs | XS | Low |
| GitHub PR comments | S | Low |
| Diff chunking | M | Medium |
| `.asdlc.yml` overrides | M | Low |
| Changed-files-only mode | M | Low |
| Fan-out workflow | M | Medium |
| Hook installer CLI | S | Low |
| Docker Compose | S | Low |
| Expanded test suite | L | Low |

**XS** = < 1 hour · **S** = half day · **M** = 1–2 days · **L** = 2–3 days
