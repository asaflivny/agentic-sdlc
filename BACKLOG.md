# asdlc — Backlog & Tracking

## ✅ Completed Work (2026-06-06)

All items below were implemented in a prior session. See [IMPROVEMENTS_SUMMARY.md](IMPROVEMENTS_SUMMARY.md) for details.

| Item | Status | Files | Notes |
|---|---|---|---|
| **Structured JSON log output** (P1) | ✅ | `main.py` | `_JsonFormatter` class, wired into `_configure_logging()` |
| **GitHub PR comment posting** (P2) | ✅ | `integrations/github.py`, `main.py` | Posts findings as review comments on PRs; inline comments optional |
| **Diff chunking for large changes** (P3) | ✅ | `config.py`, `workflows/orchestrator.py` | `DIFF_CHUNK_SIZE_KB` config var; splits diffs >25 KB with overlap |
| **Per-repo `.asdlc.yml` overrides** (P4) | ✅ | `workflows/repo_config.py`, `workflows/router.py` | Schema validation via Pydantic; cached by (repo_url, sha) |
| **Changed-files-only mode** (P4) | ✅ | `agents/base.py`, `workflows/orchestrator.py` | Per-agent `file_filter` in `AgentSpec`; pre-filters diff before analysis |
| **Hook installer CLI** (P5) | ✅ | `replay.py` | `asdlc install-hook` subcommand; validates server reachability |
| **Docker Compose setup** (P5) | ✅ | `docker-compose.yml`, `Dockerfile` | One-command local dev setup (Ollama + asdlc) |

---

## 🚀 Actual Remaining Work

### High Priority

#### 1. Fan-out workflows (run multiple workflows in parallel)
**File:** `workflows/orchestrator.py`, `workflows/base.py`, routing rules  
**Why:** A sensitive branch push should run `quick_review` + `security_focus` simultaneously, not pick one.  
**Effort:** M (1–2 days)  
**What to do:**
- Add optional `fan_out: list[str]` field to `WorkflowDefinition` (names of workflows to run in parallel)
- In `WorkflowOrchestrator.analyze()`, if `fan_out` is set, run each workflow via `asyncio.gather`
- Merge `agent_results` from all workflows; deduplicate findings across the merged set
- Add a `FanOutRule` routing rule type

#### 2. Expanded test suite
**File:** `tests/`  
**Why:** Coverage is sparse (only HMAC tests exist). Agents, orchestrator, router have zero coverage.  
**Effort:** L (2–3 days, pick any subset)  
**What to do (pick any or all):**
- `tests/test_router.py` — test `BranchPatternRule`, `FilePatternRule`, `DefaultRule` routing decisions
- `tests/test_orchestrator.py` — mock Ollama client; verify sequential context enrichment, deduplication, timeout handling
- `tests/test_agent_subgraph.py` — expand existing tests; verify tool calling, message reducer, recursion limit
- `tests/test_findings_parser.py` — parametrize `BaseAgent.extract_findings()` with model output shapes (JSON block, bare array, wrapped, malformed)
- `tests/test_store.py` — verify save/list/get round-trip on SQLite; test indices exist

---

### Medium Priority

#### 3. Agent effectiveness metrics
**File:** `templates/dashboard.html`, `store.py`, new metrics endpoint  
**Why:** "Which agents find the most bugs?" and "Is code quality trending up?" — need dashboards.  
**Effort:** S (1 day)  
**What to do:**
- Add agent-level aggregations to `WorkflowStore`: `findings_by_agent`, `avg_severity_by_agent`, `agent_effectiveness_score`
- Add `/metrics/agents` endpoint returning JSON with these aggregations
- Extend dashboard to show agent comparison table + trend charts over time

#### 4. Result comparison UI
**File:** `main.py` (new endpoint), `templates/`  
**Why:** Compare findings across two runs to spot regressions/improvements.  
**Effort:** M (1 day)  
**What to do:**
- Add `GET /compare/{run_id_1}/{run_id_2}` endpoint
- Return JSON diff: added findings, removed findings, unchanged
- Extend dashboard with side-by-side comparison view

#### 5. Model A/B testing mode
**File:** `config.py`, `workflows/orchestrator.py`  
**Why:** Compare agent output across models (qwen2.5-coder:7b vs. llama3.1:8b) to pick the best.  
**Effort:** M (1–2 days)  
**What to do:**
- Add `MODEL_AB_TEST_AGENTS: list[str]` config (empty = disabled)
- When set, run each listed agent through both models in parallel
- Store results separately; expose via API for comparison

#### 6. Request body caching (security debt)
**File:** `main.py`, `security.py`  
**Why:** Currently `await request.body()` consumes the stream in webhook verification; body isn't re-parseable.  
**Effort:** S (2–4 hours)  
**Note:** This works today but is fragile. Not a bug, but architectural debt.  
**What to do:**
- Add middleware that caches `request.body()` before verification
- Pass cached body to both `verify_webhook_signature()` and route handler

---

### Low Priority (Strategic, not immediate)

#### 7. PostgreSQL migration path
**File:** New `storage/postgres_adapter.py`, docs  
**Why:** SQLite is single-writer; PostgreSQL enables horizontal scaling + multi-instance setup.  
**Effort:** L (3–5 days)  
**What to do:**
- Document migration strategy (don't implement yet)
- Create adapter that implements same `WorkflowStore` interface
- Write migration guide for users: export SQLite → seed Postgres → point config to new DB

#### 8. Token counting via `tiktoken`
**File:** `models/results.py`, agents  
**Why:** Today `tokens_used` is always 0. Can estimate via token counting library.  
**Effort:** XS (4 hours)  
**What to do:**
- Add `tiktoken` to dependencies
- In `AgentResult`, estimate tokens from `input_messages` + `output_text` length
- Store estimate in `tokens_used`; note it's approximate

---

## Effort Legend

| Symbol | Meaning |
|---|---|
| **XS** | < 1 hour |
| **S** | ~4–6 hours (half day) |
| **M** | 1–2 days |
| **L** | 2–3 days |

---

## Next Steps

1. **For this session:** Pick one from "High Priority" or "Medium Priority"
2. **After implementing:** Update CLAUDE.md, README.md, and tests per the "Update Triggers" table in CLAUDE.md
3. **Before closing:** Mark this backlog item ✅ and move to completed section above
