# asdlc — Enhancement Plan

> Last updated: 2026-06-03

---

## Overview

Nine enhancements grouped by effort and impact. Items within each tier are ordered by recommended implementation sequence.

| # | Enhancement | Tier | Effort | Risk |
|---|---|---|---|---|
| 1 | Structured JSON logging | Quick win | XS | Low |
| 2 | GitHub PR comment posting | Quick win | S | Low |
| 3 | Concurrent push queue | Quick win | XS | Low |
| 4 | Hook installer CLI | Quick win | S | Low |
| 5 | Diff chunking | Correctness | M | Medium |
| 6 | Structured output retry | Correctness | XS | Low |
| 7 | Per-repo `.asdlc.yml` overrides | Architecture | M | Low |
| 8 | Finding trend tracking | Architecture | M | Low |
| 9 | Expanded test suite | Foundation | L | Low |

**Effort key:** XS < 1 hr · S ½ day · M 1–2 days · L 2–3 days

---

## Tier 1 — Quick Wins

### 1. Structured JSON Logging

**Problem:** Plain-text `logging.info` lines are hard to aggregate. Log aggregators (Loki, Datadog, CloudWatch) can't filter by `run_id`, `repo`, or `severity` without fragile regex.

**Approach:**
- Add `structlog` (or a custom JSON `logging.Formatter`) to `main.py`
- Every log call already passes structured kwargs — no logic changes needed, only the formatter
- Minimum fields per line: `ts`, `level`, `logger`, `event`, plus any kwargs (`run_id`, `repo`, `agent`, `findings`, etc.)

**Files:** `main.py`, optional shared `logging_config.py`

**Done when:** `uvicorn` output is newline-delimited JSON; `jq .run_id` on a log line returns the run ID.

---

### 2. GitHub PR Comment Posting

**Problem:** Findings live only in SQLite. Developers must poll `/results` — they never see feedback in their normal code review flow.

**Approach:**
- New `integrations/github.py` with an async `post_pr_comment(token, owner, repo, pr_number, body)` helper
- On workflow completion in `main.py` `_run_workflow`, if `GITHUB_TOKEN` is set, look up the open PR for the branch (`GET /repos/{owner}/{repo}/pulls?head={branch}`), then post a markdown summary
- Format: one collapsible `<details>` block per agent, severity badge per finding (`🔴 critical`, `🟡 medium`, etc.)
- Skip silently if `GITHUB_TOKEN` is unset or no matching PR is found

**New config vars (add to `.env`):**
```
# GITHUB_TOKEN=ghp_...
# GITHUB_REPO=owner/repo   # auto-detected from push payload if omitted
```

**Files:** `integrations/github.py` (new), `main.py`, `config.py`

**Done when:** A test push with findings results in a PR review comment appearing within 5 seconds of push completion.

---

### 3. Concurrent Push Queue

**Problem:** Two simultaneous pushes can spawn 10+ agents hitting the same Ollama instance, causing OOM or timeout cascades.

**Approach:**
- Add `asyncio.Semaphore` initialized from `OLLAMA_MAX_CONCURRENCY` (default `2`) in `main.py` lifespan
- Acquire the semaphore in `_run_workflow` before the first agent call; release on exit
- Log a warning if a push waits more than 30 s for the semaphore: `"push queued, waiting for slot"`

**New config var:**
```
# OLLAMA_MAX_CONCURRENCY=2
```

**Files:** `main.py`, `config.py`

**Done when:** Three concurrent test pushes serialize correctly; Ollama stays under memory limit.

---

### 4. Hook Installer CLI

**Problem:** Setting up the pre-push hook requires copy-pasting a multi-line bash script from the README — the biggest onboarding friction point.

**Approach:**
- Extend `replay.py` with `argparse` subparsers, adding an `install-hook` subcommand
- Or create a new `cli.py` and register it as `asdlc` console script in `pyproject.toml`
- Write `.git/hooks/pre-push` from an embedded template string; `chmod +x` the file
- Validate the server is reachable at `--url` (default `GIT_WEBHOOK_URL`) before writing
- Print next steps on success: set `GIT_WEBHOOK_SECRET`, do a test push

**Usage:**
```sh
asdlc install-hook /path/to/repo --url http://localhost:8088
```

**Files:** `replay.py` or new `cli.py`, `pyproject.toml`

**Done when:** `asdlc install-hook` on a fresh clone creates a working hook with a single command.

---

## Tier 2 — Correctness

### 5. Diff Chunking for Large Changes

**Problem:** Diffs over 30 KB are silently truncated in `fetch_diff`. Agents analyze an incomplete diff without knowing it — large refactors or dependency bumps lose most context.

**Approach:**
- Add `DIFF_CHUNK_SIZE_KB` config var (default `25`, set `0` to disable)
- In `orchestrator.py` `_fetch_diff`: if raw diff exceeds the threshold, split into overlapping chunks (e.g. 20 KB body + 2 KB overlap at boundaries)
- Run the agent once per chunk, collect `FindingList` from each run
- Merge all findings lists and pass through existing `_deduplicate_findings`
- Log a warning when chunking activates: `"diff too large (N KB), splitting into M chunks"`

**New config var:**
```
# DIFF_CHUNK_SIZE_KB=25
```

**Files:** `workflows/orchestrator.py`, `tools/git_tools.py`, `config.py`

**Done when:** A 60 KB diff produces findings from all three chunks; no silent truncation; log shows chunk count.

---

### 6. Structured Output Retry

**Problem:** When `with_structured_output` returns 0 findings but `summary` is non-empty, the system silently loses information (known issue with `qwen2.5-coder:7b`).

**Approach:**
- In `agents/base.py` `extract` node: if `extraction.findings` is empty and `summary` is non-empty, retry once with an explicit JSON-mode prompt that quotes the summary and demands the schema
- Add `EXTRACTION_RETRY=true` config var (default `true`) to allow disabling for faster local testing
- Log `"extraction returned 0 findings, retrying with explicit JSON prompt"` on retry

**Files:** `agents/base.py`, `config.py`

**Done when:** Integration test with a deliberately minimal model still surfaces at least one finding when the prose contains a clear issue.

---

## Tier 3 — Architecture

### 7. Per-Repo `.asdlc.yml` Overrides

**Problem:** Routing rules are global and hard-coded. Teams can't opt specific repos into a different workflow, disable agents, or add file-pattern rules without touching server code.

**Schema:**
```yaml
workflow: full_review          # override which workflow to use
agents:
  exclude: [test_coverage]     # drop specific agents by name
routing:
  - pattern: "*.sol"           # extra file-pattern rule (repo-local)
    workflow: security_focus
```

**Approach:**
- New `workflows/repo_config.py` — reads `.asdlc.yml` from repo root at `HEAD` via `get_file_content` (already available)
- Parse with `pyyaml`; fall back to global routing if file is missing, malformed, or empty
- Wire into `workflows/router.py`: after global routing resolves a workflow, apply repo overrides (agent exclusions, workflow swap)
- Cache parsed config per `(repo_url, after_sha)` for the lifetime of the run

**Files:** `workflows/repo_config.py` (new), `workflows/router.py`, `pyproject.toml` (add `pyyaml`)

**Done when:** A test repo with `.asdlc.yml: workflow: security_focus` routes to `security_focus` even on a non-sensitive branch.

---

### 8. Finding Trend Tracking

**Problem:** The `WorkflowStore` persists runs but doesn't track findings over time. There's no way to tell if a file's bug count is improving or getting worse across pushes.

**Approach:**
- Add a `findings` table to the SQLite schema: `(id, run_id, file_path, title, severity, created_at)`
- Populate it in `store.py` `save_workflow_result` from each agent's `findings` list
- New endpoint `GET /repos/{repo_name}/findings/trend?days=30` — returns finding counts grouped by `(date, severity)`
- Expose trend data on the dashboard (`templates/dashboard.html`) as a simple sparkline or count table

**Files:** `store.py`, `main.py`, `templates/dashboard.html`

**Done when:** After 3+ pushes to the same repo, the trend endpoint returns a time-bucketed count per severity.

---

## Tier 4 — Foundation

### 9. Expanded Test Suite

**Problem:** Only 7 HMAC tests exist. Agents, orchestrator, and router have zero coverage — changes to core logic can't be validated without a manual test push.

**Priority order:**

| Test file | What to cover |
|---|---|
| `tests/test_router.py` | `BranchPatternRule`, `FilePatternRule`, `DefaultRule` with fixture `PushEvent` objects |
| `tests/test_store.py` | Save / list / get round-trip on a real SQLite file via `tmp_path` fixture |
| `tests/test_orchestrator.py` | Mock Ollama client; verify sequential context enrichment, dedup, timeout |
| `tests/test_dep_auditor.py` | Mock `httpx` OSV responses; verify CVE finding generation |
| `tests/test_agent_subgraph.py` | Smoke-test the LangGraph subgraph compile and node transitions |

**Files:** `tests/` (all new files)

**Done when:** `pytest tests/` covers router, store, and orchestrator; coverage report shows > 60% on core modules.

---

## Implementation Order

For maximum near-term value with minimum risk:

```
Phase 1 (this week):   1 → 3 → 6         # log hygiene + safety + correctness fix
Phase 2 (next week):   2 → 4             # user-visible features
Phase 3 (later):       5 → 7 → 8         # architecture depth
Phase 4 (ongoing):     9                 # test coverage grows with each change
```
