# Agentic SDLC (asdlc) — Developer Guide

## Project Overview

A FastAPI webhook server that automatically analyzes git pushes using local LLM agents (via Ollama). Clients install a pre-push hook that sends a GitHub-compatible payload. The server routes the push to a workflow (full_review, quick_review, security_focus), runs agents in parallel or sequentially, and logs findings to stdout.

## Directory Structure

```
├── main.py                         # FastAPI app, webhook endpoint, background task dispatch
├── config.py                       # Pydantic settings + defaults from .env
├── security.py                     # HMAC-SHA256 signature verification
├── store.py                        # WorkflowStore — SQLite persistence for workflow results
├── replay.py                       # asdlc-replay CLI — replay a push event JSON against the server
├── templates/
│   └── dashboard.html              # HTML dashboard served at GET /
├── models/
│   ├── events.py                   # PushEvent, Commit, Repository, Pusher models
│   └── results.py                  # Finding, FindingList, AgentResult, WorkflowResult, AgentContext
├── agents/
│   ├── base.py                     # BaseAgent — LangGraph subgraph (call_model ↔ tools → extract)
│   ├── code_reviewer.py            # CodeReviewAgent (bugs, logic, best practices)
│   ├── security_analyst.py         # SecurityAnalystAgent (OWASP, secrets, crypto)
│   ├── performance_analyst.py      # PerformanceAnalystAgent (algorithms, I/O, memory)
│   ├── dep_auditor.py              # DependencyAuditorAgent (OSV.dev CVE checks, no LLM)
│   └── test_coverage.py            # TestCoverageAgent (flags modified files with no test)
├── workflows/
│   ├── base.py                     # WorkflowDefinition, AgentSpec, ExecutionMode
│   ├── router.py                   # WorkflowRouter with BranchPatternRule and FilePatternRule
│   ├── orchestrator.py             # WorkflowOrchestrator (sequential/parallel execution)
│   └── definitions/
│       ├── full_review.py          # All 5 agents, sequential (main/master/release/*)
│       ├── security_focus.py       # Security + performance, parallel (sensitive files)
│       └── quick_review.py         # Code reviewer only, can delegate (default)
├── tools/
│   ├── base.py                     # ToolDefinition, ToolParameter, ToolResult
│   ├── git_tools.py                # fetch_diff, get_file_content, list_changed_files
│   ├── agent_tool.py               # AgentTool wrapper for calling agents as sub-agents
│   └── langchain_adapter.py        # to_langchain_tool() — bridges ToolDefinition → StructuredTool
├── integrations/
│   ├── github.py                   # GitHub PR review comments
│   ├── jenkins.py                  # Jenkins callback, JUnit XML, API integration
│   └── (email, Slack via main.py notifiers)
├── tests/
│   └── test_security.py            # HMAC validation tests (7 cases)
├── docs/
│   └── JENKINS_INTEGRATION.md      # Jenkins integration guide
├── examples/
│   ├── Jenkinsfile.groovy          # Example Jenkinsfile
│   ├── asdlc-shared-library.groovy # Jenkins shared library helper
│   └── asdlc-webhook-handler.groovy # Example webhook receiver job
├── README.md                       # User documentation
├── pyproject.toml                  # Dependencies and metadata
└── .env                            # Local configuration (WEBHOOK_SECRET, OLLAMA_BASE_URL, etc.)
```

## Key Design Patterns

### 1. Agentic Loop (agents/base.py)

Each agent runs as a **LangGraph subgraph** with three nodes:

```
START → call_model ──(tool_calls?)──→ tools → call_model (loop)
                  └──(no tools)────→ extract → END
```

1. **`call_model`** — invokes `llm.bind_tools(lc_tools)` with the full message history; after the response, `_coerce_text_tool_call` is applied to promote any tool call the model emitted as a fenced JSON block (qwen2.5-coder quirk) into a real `tool_calls` entry so `ToolNode` can execute it
2. **`tools`** — LangGraph's built-in `ToolNode` executes tool calls; results appended as `ToolMessage`s; includes git tools, agent delegation tools, and RAG search (when enabled)
3. **`extract`** — a second LLM call using `llm.with_structured_output(FindingList)` turns the agent's final prose into typed `Finding` objects; no regex parsing; supports retry with explicit JSON schema if first attempt yields 0 findings but prose exists

The loop depth is controlled by `config.agent_recursion_limit` (default 25 super-steps). Hitting it raises `GraphRecursionError`, which is caught and returns 0 findings with a warning log.

Tool executors live in `tools/git_tools.py`, `tools/agent_tool.py`, and `tools/rag_tools.py`. They return `ToolResult`. `to_langchain_tool()` in `tools/langchain_adapter.py` wraps each `(ToolDefinition, executor)` pair into a LangChain `StructuredTool` consumed by `ToolNode`. The wrapper supports `arg_overrides` to inject runtime values like `repo_url` and `rag_store`.

**RAG Integration** (when `config.rag_enabled=True`):
- `BaseAgent` automatically registers the `search_knowledge` tool via `_register_rag_tool()`
- Tool is wrapped to track retrieved documents in `self._knowledge_retrieved` for audit trails
- RAG results are formatted as JSON with content snippets (500 chars), metadata, and relevance scores
- Errors in RAG searches are caught and returned as tool errors; agent can see them and proceed without knowledge

### 2. Workflow Routing (workflows/router.py)

`RoutingRule` subclasses match push events to workflows:

- **BranchPatternRule** — regex match on `event.branch`
- **FilePatternRule** — regex search across all changed files
- **DefaultRule** — always matches (fallback to quick_review)

Rules are sorted by priority; first match wins.

### 3. Sequential vs. Parallel (workflows/orchestrator.py)

- **Sequential:** Agents run one at a time; each receives `additional_context` from the prior agent's findings (capped at 2000 chars) plus `shared_context` (RAG knowledge from repository's known issues, seeded at workflow start)
- **Parallel:** All agents run concurrently via `asyncio.gather`; no additional context passed between them, but all share the same `shared_context` (RAG knowledge seeded from repository)

### 4. Finding Extraction

Findings are extracted via **structured output**, not regex. After the agentic loop ends, the `extract` node calls:

```python
structured_llm = self.llm.with_structured_output(FindingList)
extraction: FindingList = await structured_llm.ainvoke([system_msg, HumanMessage(content=summary)])
```

`FindingList` is a Pydantic model in `models/results.py`:

```python
class FindingList(BaseModel):
    findings: list[Finding] = []
```

Each `Finding` has: `title`, `description`, `severity` (Severity enum), optional `file_path` / `line_number`, `recommendation`.

There is **no `---FINDINGS---` sentinel** and no fallback regex parser — the model fills the schema directly.

## Running Locally

```sh
# Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Set up Ollama
ollama pull qwen2.5-coder:7b

# Start the server
.venv/bin/uvicorn main:app --port 8088 --reload

# In another terminal, test with a push to inventory-tracker
cd ../inventory-tracker
git commit --allow-empty -m "test"
git push

# Watch the server logs for the analysis
```

## Jenkins Integration

Bi-directional integration with Jenkins:

1. **Jenkins → asdlc**: POST to `/scan` endpoint with repo path and callback URL
2. **asdlc → Jenkins**: POST findings to callback URL in JUnit XML + JSON format, plus optional Jenkins API calls to set build status

### Quick Start

```groovy
// In your Jenkinsfile
@Library('asdlc-shared-library') _

asdlcScan(
    repo_path: "${WORKSPACE}",
    branch: "${GIT_BRANCH}",
    asdlc_url: "http://localhost:8088",
    asdlc_api_key: "your-api-key",
    jenkins_api_token: "your-jenkins-token"
)
```

Findings are published as:
- **JUnit XML** — parsed as test failures by Jenkins UI
- **JSON report** — archived artifact with full details
- **Build description** — updated via Jenkins API (if token provided)

See [JENKINS_INTEGRATION.md](docs/JENKINS_INTEGRATION.md) for complete setup.

## Testing

```sh
.venv/bin/pytest tests/
```

Currently covers HMAC signature validation. To add tests:

1. Create fixtures for `PushEvent` (models/events.py already has them)
2. Test specific agents via `AgentContext` + a mocked `BaseChatModel`
3. Test workflows end-to-end via `WorkflowOrchestrator` + a local test repo

## Common Gotchas

### 1. Context Truncation

In sequential workflows, findings are passed as a string capped at 2000 chars (`orchestrator.py` near `context.additional_context = ...[:2000]`). If findings JSON is long, the next agent may see incomplete prior findings. Raise the limit cautiously — it increases the next agent's prompt size.

### 2. Structured Extraction Failures

`with_structured_output` relies on the model supporting tool/function calling or JSON mode. With small local models (`qwen2.5-coder:7b`) the extraction step can return 0 findings even when the agent wrote a valid prose analysis. **Automatic Retry** (when `config.extraction_retry=True`):
- If the first extraction returns 0 findings but prose exists, a second extraction is triggered with an explicit JSON schema prompt
- The retry includes the exact Pydantic schema to guide JSON generation
- If retry also fails, findings remain at 0 but the original prose is preserved in `AgentResult.summary`

If structured extraction consistently fails:
- Check logs for `structured extraction failed` or `extraction_retry_success` messages
- Try a larger or more instruction-following model (e.g. `qwen2.5:32b`, `llama3.1:8b`)
- Inspect the agent's `summary` field in the stored result — the prose is always preserved

### 2a. Model Emits Tool Calls as Text

`qwen2.5-coder` on Ollama sometimes emits a `\`\`\`json {"name": ..., "arguments": ...}\`\`\`` block in the message content instead of using the native tool-calling API. `_coerce_text_tool_call` in `base.py` detects this shape and promotes it to a real `tool_calls` entry so `ToolNode` executes it. Watch logs for `"agent emitted tool call as text, coercing: ..."` to confirm this path is hit.

### 3. Git Tool Failures

Git tools resolve `repo_url` locally (checks for `.git` directory or bare repo). If the repo isn't accessible from the server's filesystem, all git tools fail. Ensure:
- Webhook payload has correct `clone_url`
- `clone_url` points to a local path (`/path/to/repo` or `/path/to/repo.git`)

### 4. Async Tool Execution

All tool executors must be `async def`. They are wrapped by `to_langchain_tool()` and invoked by LangGraph's `ToolNode`. A sync function will block the event loop. This applies to:
- Git tools (`fetch_diff`, `get_file_content`, `list_changed_files`)
- Sub-agent delegation tools (`AgentTool.execute`)
- RAG search tools (`search_knowledge` wrapped in `BaseAgent._register_rag_tool`)

Errors surface as `"ERROR: ..."` strings in tool output rather than exceptions — the agent sees the error and can decide how to proceed. The wrapper in `to_langchain_tool` ensures the coroutine is properly awaited and `ToolResult.is_error` is converted to an error prefix.

### 5. Recursion Limit

`agent_recursion_limit` (default 25) counts LangGraph super-steps, not tool calls. Each `call_model → tools` round is 2 super-steps. A limit of 25 allows ~12 tool-call rounds. If agents hit the limit on complex diffs, raise `AGENT_RECURSION_LIMIT` in `.env`.

### 6. Jenkins Callback Failures

When using Jenkins integration, callback POST may fail if:
- Jenkins is behind a firewall or NAT (asdlc server cannot reach callback URL)
- Jenkins webhook token doesn't match configured value in Generic Webhook Trigger plugin
- Jenkins API token is invalid or lacks Job.EXTENDED_READ permissions

Check asdlc logs for `jenkins callback failed` or `jenkins set_build_status failed` messages. Ensure callback URL is reachable from the asdlc server's network.

## Adding a New Agent

1. Create `agents/my_agent.py`:

```python
from langchain_core.language_models import BaseChatModel
from agents.base import BaseAgent
from config import Settings
from tools.git_tools import FETCH_DIFF, LIST_FILES, fetch_diff, list_changed_files

class MyAgent(BaseAgent):
    name = "my_agent"
    display_name = "My Analyzer"
    description = "Analyzes code for X"

    def __init__(self, llm: BaseChatModel, config: Settings):
        super().__init__(llm, config)
        self._register_tool(FETCH_DIFF, fetch_diff)
        self._register_tool(LIST_FILES, list_changed_files)

    def get_system_prompt(self) -> str:
        return "You are an expert in X. Analyze the code changes and identify issues."
```

2. **Register in `workflows/orchestrator.py`** (do this in the same edit, never defer):

```python
AGENT_REGISTRY["my_agent"] = MyAgent
```

3. Add to a workflow definition (e.g., `workflows/definitions/full_review.py`):

```python
agent_specs=[
    AgentSpec(agent_class=CodeReviewAgent),
    AgentSpec(agent_class=MyAgent),
    ...
]
```

## Debugging Agents

Enable verbose logging:

```sh
LOG_LEVEL=DEBUG .venv/bin/uvicorn main:app --port 8088
```

Watch for:
- `=== [agent_name] SYSTEM PROMPT ===` — agent's role
- `=== [agent_name] USER INPUT ===` — initial context + diff preview
- `=== [agent_name] LLM RESPONSE (turn=N, tool_calls=...) ===` — each LangGraph step, shows tool calls if any
- `agent=X findings=N knowledge_used=K` — how many findings extracted, how many RAG documents retrieved
- `structured extraction failed` — extraction error (findings will be 0; prose in `summary`)
- `extraction_retry_success` — retry with explicit JSON schema succeeded after first attempt returned 0 findings
- `agent=X hit recursion_limit=N` — agent stopped mid-analysis; raise `AGENT_RECURSION_LIMIT`
- `rag_search_completed collection=... results=N` — RAG search executed successfully
- `Failed to retrieve knowledge from RAG` — RAG store error (non-fatal; agent proceeds without knowledge)

### Quick diagnostic commands

```sh
# Tail SQLite store for recent runs
sqlite3 asdlc.db "SELECT run_id, repo_name, workflow_name, created_at FROM workflow_runs ORDER BY created_at DESC LIMIT 10;"

# Replay a saved push event
.venv/bin/asdlc-replay push_event.json --url http://localhost:8088

# Check Ollama is serving the right model
curl http://localhost:11434/api/tags | python3 -m json.tool

# Manually trigger a full scan without a git push
curl -X POST http://localhost:8088/scan \
  -H "X-API-Key: $ASDLC_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/absolute/path/to/repo"}'

# Health / readiness
curl http://localhost:8088/healthz
curl http://localhost:8088/readyz
```

## Invariants (must be enforced on every edit)

1. **New agent = orchestrator registration** — any new `BaseAgent` subclass in `agents/` must be added to `AGENT_REGISTRY` in `workflows/orchestrator.py` in the same edit session.

2. **New config var = `.env` comment** — whenever a field is added to `Settings` in `config.py`, add a commented example to `.env`. The `.env` file is the canonical "what can I configure" reference. Examples: `ENABLE_CHECKPOINTING`, `API_KEY`, `RAG_ENABLED`.

3. **Tool executors must be `async def`** — `to_langchain_tool()` wraps executors with `await`. Sync functions block the event loop. This includes RAG search tools wrapped in `BaseAgent._register_rag_tool()`.

4. **30 KB diff cap is intentional** — `fetch_diff` truncates to `[:30000]`. Raising it risks Ollama context overflow. Test with a large repo before increasing.

5. **`get_settings()` is `@lru_cache`** — adding env vars at runtime won't be picked up. Tests that need different settings must construct `Settings()` directly.

6. **Sequential context cap is intentional** — `context.additional_context[:2000]` in orchestrator.py prevents the next agent's prompt from growing unbounded. Compress findings before increasing this limit.

7. **RAG store is optional** — agents accept `rag_store=None` and gracefully degrade. If `config.rag_enabled=True` but no store is provided, tools return errors that agents can see. In production, seed the RAG store before calling `WorkflowOrchestrator`.

## Claude Code Agent Usage

When working in this codebase, use specialized subagents as follows:

- **Designing a new agent or workflow** → use `Plan` mode (`/plan`) before writing code; the graph topology (which nodes, which edges) is worth agreeing on first.
- **Broad codebase search** (>3 grep queries needed) → spawn `Explore` subagent.
- **Security review of webhook or auth changes** → run `/security-review` skill.
- **Tracking multi-step implementation work** → use `TaskCreate` to break it into steps and `TaskUpdate` as each step completes.
- **Checking if an agent change broke the graph** → run `.venv/bin/pytest tests/` first; add an agent-level smoke test if none exists.

## Known Issues (as of 2026-06-06)

- **Token counting** — `tokens_used` is always 0; LangGraph / Ollama doesn't surface usage in a consistent way across models
- **Structured extraction on small models** — `qwen2.5-coder:7b` sometimes returns an empty `findings` list even after prose analysis and retry; the `summary` field preserves the prose for manual review. Workaround: use `qwen2.5:32b`, `llama3.1:8b`, or enable `AGENT_USE_TOOLS=false` to avoid complex multi-turn loops
- **Delegation infinite loop** — `quick_review` can delegate to security/performance via `AgentTool`, but those agents cannot delegate back (only `quick_review` has `can_call` set in its `AgentSpec`)
- **RAG embedding latency** — first search on a collection triggers embedding model load (~5-10s); subsequent searches are fast. Disable RAG for quick local testing

## Checkpointing & Resumable Runs

Both workflow and agent graphs support **LangGraph checkpointing** for resumable runs. This allows the server to recover mid-analysis after a crash or restart without losing progress.

**How it works:**
- Each agent run generates a stable `thread_id` based on agent name + repo + commit SHA + diff hash
- Graph state (messages, findings, intermediate results) is persisted to `checkpoint_db_path` (default: `asdlc_checkpoints.db`)
- If a run is interrupted, calling `ainvoke` with the same `thread_id` will resume from the last checkpoint
- Checkpointing is enabled by default (`enable_checkpointing: True` in `config.py`)

**Configuration:**
```bash
# Enable/disable checkpointing
ENABLE_CHECKPOINTING=true
CHECKPOINT_DB_PATH=asdlc_checkpoints.db
```

**Behavior:**
- **Enabled** — each agent/workflow run is saved to the checkpoint DB; safe for production
- **Disabled** — graphs are not persisted; faster for quick local testing

Checkpointing has negligible performance impact and is recommended for long-running or critical analyses.

## RAG (Retrieval-Augmented Generation)

The system supports optional RAG to augment agent analysis with domain knowledge.

**How it works:**
- Each agent automatically registers the `search_knowledge` tool when `config.rag_enabled=True`
- Before workflow execution, `WorkflowOrchestrator.analyze()` seeds `shared_context` with known issues for the repository (query: repo name, collection: "known_issues")
- Agents can call `search_knowledge` to query a Chroma vector database by collection name (e.g. "architecture_patterns", "known_issues", "best_practices")
- Results include content (truncated to 500 chars), metadata (source, tags, etc.), and relevance scores
- Retrieved documents are tracked in `AgentResult.knowledge_used` for audit trails

**Configuration:**
```bash
RAG_ENABLED=true                                    # Enable RAG (default: true)
RAG_DB_PATH=./asdlc_rag.db                          # Chroma persistent store
RAG_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2  # HuggingFace embeddings
RAG_CHUNK_SIZE=500                                  # Characters per document chunk
RAG_SIMILARITY_THRESHOLD=0.75                       # Threshold for filtering results
RAG_SEARCH_LIMIT=5                                  # Default results per query
```

**Seeding the knowledge base** (manual or external):
- Use the `RAGStore` API in `tools/rag_tools.py` to add documents: `await store.add_documents(collection, documents, metadata)`
- Collections are isolated (e.g. "architecture_patterns" vs "known_issues")
- Documents are chunked and embedded on insertion
- Metadata (source, tags, repo, etc.) is preserved for filtering and audit

If RAG is disabled or the store is unavailable, agents see a graceful error in tool output and proceed without knowledge.

## Performance Tips

1. **Parallel execution** — `security_focus` runs 2 agents concurrently; watch CPU/memory with large diffs
2. **Diff size** — diffs are capped at 30 KB; large refactors lose context silently; disable chunking with `DIFF_CHUNK_SIZE_KB=0` if you're seeing timeouts
3. **Recursion limit** — default 25 super-steps ≈ 12 tool-call rounds per agent; raise `AGENT_RECURSION_LIMIT` for deep analysis
4. **Model selection** — `qwen2.5-coder:7b` is fast (~8 GB VRAM); try `qwen2.5:32b` or `llama3.1:8b` for better structured-output reliability
5. **Checkpointing overhead** — checkpoint writes add ~50-100ms per graph step; disable for quick local testing with `ENABLE_CHECKPOINTING=false`
6. **RAG search latency** — vector similarity search adds ~100-500ms per query depending on database size; disable with `RAG_ENABLED=false` for minimal latency

## Framework Currency Rule

Before modifying `pyproject.toml`, adding a new dependency, upgrading a package, or when a task involves any of the core frameworks below, **search the web for the latest stable version and any breaking changes or new features relevant to this project**.

Core frameworks to check:

| Package | What to look for |
|---|---|
| `fastapi` | New routing, lifespan API, response model changes |
| `pydantic` / `pydantic-settings` | v2 migration notes, validator API, model config |
| `langgraph` | Graph API changes, `ToolNode` signature, `StateGraph` compile options, checkpointing API |
| `langchain-core` | `BaseChatModel`, `StructuredTool`, `bind_tools`, `with_structured_output` API changes |
| `langchain-ollama` | `ChatOllama` constructor, `base_url` vs `host` param, model name format |
| `openai` (SDK) | Still used as the OpenAI-compat client in orchestrator; check `AsyncOpenAI` constructor |
| `uvicorn` | Worker config, TLS, HTTP/2 support |
| `httpx` | Async client changes, auth, timeout API |
| `aiosqlite` | Connection/cursor API |
| `jinja2` | Template API, environment options |
| `ollama` (Ollama server) | New model support, API endpoint changes, structured output support per model |

**How to apply this rule:**
1. Use `WebSearch` to query `<package> latest version changelog` or `<package> release notes`.
2. Compare the found version against the specifier in `pyproject.toml`.
3. If a newer major/minor is available, note breaking changes before suggesting an upgrade.
4. For Ollama model updates, check [ollama.com/library](https://ollama.com/library) for new `qwen2.5-coder` or reasoning model variants.

Do this proactively — don't wait to be asked.

## Update Triggers

When any of these files change, update the corresponding targets **in the same session** before closing.

### CLAUDE.md

| File changed | Section(s) to review |
|---|---|
| `agents/base.py` | Key Design Patterns §1, Common Gotchas §4–5, Adding a New Agent, Debugging |
| `models/results.py` | Key Design Patterns §4, Adding a New Agent template |
| `tools/langchain_adapter.py` | Key Design Patterns §1, Common Gotchas §4 |
| `workflows/orchestrator.py` | Key Design Patterns §3, Invariants §1, Known Issues |
| `workflows/router.py` | Key Design Patterns §2 |
| `config.py` | Invariants §2 and §5, Performance Tips, Framework Currency Rule table |
| `pyproject.toml` | Framework Currency Rule table (check new/removed packages) |
| `main.py` | Running Locally, Known Issues, Jenkins Integration section |
| `agents/<new_file>.py` | Directory Structure, Adding a New Agent, Invariants §1 |
| `workflows/definitions/<new_file>.py` | Directory Structure |
| `integrations/jenkins.py` | Directory Structure, Common Gotchas §6 |

### README.md

| File changed | Section(s) to review |
|---|---|
| `agents/base.py` | Architecture diagram |
| `agents/<new_file>.py` | "What it does" agent table |
| `workflows/definitions/<new_file>.py` | "Workflow routing" table |
| `main.py` | API section (new endpoints, changed behaviour) |
| `config.py` | Configuration / Quick start sections |
| `docker-compose.yml` / `Dockerfile` | Docker setup section |

### Tests

| File changed | Test file to create or update |
|---|---|
| `agents/base.py` | `tests/test_agent_subgraph.py` |
| `tools/langchain_adapter.py` | `tests/test_langchain_adapter.py` |
| `security.py` | `tests/test_security.py` |
| `workflows/router.py` | `tests/test_router.py` *(missing — see BACKLOG.md)* |
| `workflows/orchestrator.py` | `tests/test_orchestrator.py` *(missing — see BACKLOG.md)* |
| `agents/dep_auditor.py` | `tests/test_dep_auditor.py` *(missing — see BACKLOG.md)* |
| `agents/<new_file>.py` | `tests/test_<agent_name>.py` |
| `tools/<new_file>.py` | `tests/test_<tool_name>.py` |

Any new `.py` file in `agents/` or `tools/` that has no corresponding `tests/test_*.py` should be noted in BACKLOG.md under "Expanded test suite."

## Future Enhancements

- [ ] Result notifications (Slack, email, PR comments)
- [ ] Multiple model support (fallback, specialized per agent)
- [ ] Context compression (summarize long findings for sequential mode)
- [ ] Pluggable rules engine (more flexible routing)
- [ ] Structured output retry when extraction returns 0 findings
