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
│   └── results.py                  # Finding, AgentResult, WorkflowResult, AgentContext
├── agents/
│   ├── base.py                     # BaseAgent (agentic loop with tool calling)
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
│   └── agent_tool.py               # AgentTool wrapper for calling agents as sub-agents
├── tests/
│   └── test_security.py            # HMAC validation tests (7 cases)
├── README.md                       # User documentation
├── pyproject.toml                  # Dependencies and metadata
└── .env                            # Local configuration (WEBHOOK_SECRET, OLLAMA_BASE_URL, etc.)
```

## Key Design Patterns

### 1. Agentic Loop (agents/base.py)

Each agent runs an OpenAI-compatible agentic loop:

1. **Initial message** — repo context + diff + prior findings (if sequential)
2. **System prompt** — agent's role and responsibilities
3. **Tool definitions** — git and delegation tools exposed as OpenAI-compatible schemas
4. **Loop:**
   - Call LLM with messages, system prompt, tools
   - Parse tool calls from response
   - Execute tools (async subprocess for git, tool delegation)
   - Append tool results to messages
   - Repeat until LLM stops calling tools or hits MAX_TOKENS

### 2. Workflow Routing (workflows/router.py)

RoutingRule subclasses match push events to workflows:

- **BranchPatternRule** — regex match on event.branch
- **FilePatternRule** — regex search across all changed files
- **DefaultRule** — always matches (fallback to quick_review)

Rules are sorted by priority; first match wins.

### 3. Sequential vs. Parallel (workflows/orchestrator.py)

- **Sequential:** Agents run one at a time; each receives `additional_context` from the prior agent's findings (capped at 2000 chars)
- **Parallel:** All agents run concurrently; no additional context passed between them

### 4. Finding Extraction

Each agent's final response is parsed for findings:

```python
# Agent response should contain:
---FINDINGS---
[
  {"title": "...", "description": "...", "severity": "high", "recommendation": "..."},
  ...
]
```

If the sentinel is missing or JSON is malformed, fallback parser tries to extract a bare JSON array. If that fails, the agent returns 0 findings.

## Running Locally

```sh
# Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Set up Ollama
ollama pull qwen2.5-coder:7b

# Start the server
uvicorn main:app --port 8080

# In another terminal, test with a push to inventory-tracker
cd ../inventory-tracker
git commit --allow-empty -m "test"
git push

# Watch the server logs for the analysis
```

## Testing

```sh
pytest tests/
```

Currently covers HMAC signature validation. To add tests:

1. Create fixtures for PushEvent (models/events.py already has them)
2. Test specific agents via AgentContext + mocked client
3. Test workflows end-to-end via WorkflowOrchestrator + test repo

## Common Gotchas

### 1. Context Truncation

In sequential workflows, findings are passed as a string capped at 2000 chars. If findings JSON is long, the next agent may not see the complete prior findings. Workaround: increase the limit in orchestrator.py:100 (currently `context.additional_context = ...[:2000]`).

### 2. Model Inconsistency

`qwen2.5-coder:7b` sometimes:
- Emits `---FINDINGS---` but with invalid JSON after it
- Emits bare JSON arrays/dicts without the sentinel
- Returns a tool-call-shaped response instead of text + findings

The fallback parser handles bare arrays but not dicts. Consider:
- Tightening the system prompt to emphasize the exact format
- Trying a different model (e.g., `qwen2.5:32b`)
- Adding a retry loop if 0 findings are parsed

### 3. Git Tool Failures

Git tools resolve repo_url locally (checks for .git directory or uses it as a bare repo path). If the repo isn't cloned or accessible from the server's filesystem, all git tools fail and the agent can't analyze. Ensure:
- Webhook payload has correct clone_url (used by agents)
- clone_url points to a local path (e.g., `/path/to/repo` or `/path/to/repo.git`)

### 4. Async Tool Execution

All tools (git, delegation) are async. Agents await them in _execute_tool. If a tool raises an exception, the result is marked `is_error=True` and the agent sees the error message as tool output (doesn't crash the agent).

## Adding a New Agent

1. Create `agents/my_agent.py`:

```python
from agents.base import BaseAgent
from models.results import AgentContext, AgentResult, Finding, Severity
from tools.git_tools import FETCH_DIFF, LIST_FILES

class MyAgent(BaseAgent):
    name = "my_agent"
    display_name = "My Analyzer"
    description = "Analyzes code for X"

    def __init__(self, client, config):
        super().__init__(client, config)
        self._register_tool(FETCH_DIFF, fetch_diff)
        self._register_tool(LIST_FILES, list_changed_files)

    def get_system_prompt(self) -> str:
        return """You are an expert in X. Analyze the code changes and identify issues..."""
```

2. Register in workflows/orchestrator.py:

```python
AGENT_REGISTRY["my_agent"] = MyAgent
```

3. Add to a workflow definition (e.g., workflows/definitions/full_review.py):

```python
agent_specs=[
    AgentSpec(agent_class=CodeReviewAgent),
    AgentSpec(agent_class=MyAgent),
    ...
]
```

## Debugging Agents

Enable verbose logging in main.py:

```python
logging.basicConfig(level=logging.DEBUG)  # default is INFO
```

Watch for:
- `=== SYSTEM PROMPT ===` — agent's role
- `=== USER INPUT ===` — initial context + diff preview
- `TURN N` — agentic loop iterations
- `tool calls: [...]` — which tools the agent invoked
- `result: N findings` — how many findings were extracted

If an agent produces 0 findings consistently:
- Check the log for malformed JSON or missing sentinel
- Verify the LLM generated valid JSON
- Try a different model or adjust the system prompt

## Known Issues (as of 2026-05-31)

See README.md "Known limitations" section for user-facing issues. Additional developer notes:

- **Token counting** — OpenAI-compatible API doesn't return token usage for Ollama responses, so `tokens_used` is always 0
- **Error handling** — tool execution exceptions are caught and returned as error results; agents see them as tool output and can retry or give up
- **Delegation infinite loop** — quick_review can delegate to security/performance, but those agents can't delegate back (only quick_review has `can_call` set)

## Performance Tips

1. **Parallel execution** — security_focus runs 2 agents in parallel; watch CPU/memory if agents are heavy
2. **Diff size** — diffs are capped at 30KB; if you're seeing "truncated" messages in logs, the diff was too large
3. **Timeout tuning** — AGENT_TIMEOUT_SECONDS (default 180s) applies per agent; sequential workflows with 5 agents can take up to 15 minutes
4. **Model selection** — `qwen2.5-coder:7b` is small and fast (~8GB VRAM); try `qwen2.5:32b` or `neural-chat` for better quality at higher cost

## Future Enhancements

- [ ] Result notifications (Slack, email, PR comments)
- [ ] Multiple model support (fallback, specialized)
- [ ] Context compression (summarize long findings for sequential mode)
- [ ] Pluggable rules engine (more flexible routing)
