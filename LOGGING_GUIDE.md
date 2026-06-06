# Verbose Logging Guide

Set `LOG_LEVEL=DEBUG` to enable comprehensive tracing of workflows, agents, tools, and decisions:

```sh
LOG_LEVEL=DEBUG .venv/bin/uvicorn main:app --port 8088
```

## Routing & Workflow Init

When a push arrives, watch for routing decision:

```
routing decision repo=inventory-tracker branch=main files_changed=5
routed repo=inventory-tracker branch=main → workflow=full_review (rule=main_branch)
rule_details rule=main_branch agents=5 mode=sequential
```

Then workflow initialization with repo config and agent lineup:

```
workflow=full_review repo=inventory-tracker branch=main mode=sequential agents=5 run_id=abc-123
git_diff_fetched size=12847 bytes
agents_built count=5 execution_mode=sequential
```

## Agent Initialization

For each agent in the workflow, you'll see model assignment and sub-agent registration:

```
agent_init order=1 name=code_reviewer model=qwen2.5-coder:7b
agent_init order=2 name=security_analyst model=qwen2.5-coder:7b
sub_agent_registered parent=code_reviewer child=security_analyst model=qwen2.5-coder:7b
```

## Agent Execution (Sequential Mode)

When running in sequential mode, each agent runs in order with context enrichment:

```
agent_start agent=code_reviewer diff_size_kb=12 context_bytes=0
=== [code_reviewer] SYSTEM PROMPT ===
...system prompt content...
=== [code_reviewer] USER INPUT ===
...user input with diff...
```

Then monitor the ReAct loop (model calling tools, recursion):

```
agent=code_reviewer turn=1 tool_calls=['fetch_git_diff'] content_len=512
tool_call agent=code_reviewer tool=fetch_git_diff args_bytes=142
agent=code_reviewer turn=2 tool_calls=none content_len=1024
```

Tool execution:

```
tool_fetch_diff mode=range before=abc1234 after=def5678 repo=inventory-tracker
tool_fetch_diff success repo=inventory-tracker size_bytes=5280
tool_list_changed_files mode=range before=abc1234 after=def5678 repo=inventory-tracker
tool_list_changed_files success file_count=8
```

Structured extraction (finding synthesis):

```
extraction_start agent=code_reviewer summary_bytes=2145
extraction_success agent=code_reviewer findings=3 severity_dist={'critical': 1, 'high': 2, 'medium': 0, 'low': 0, 'info': 0}
```

Retry on zero findings:

```
extraction returned 0 findings, retrying with explicit JSON prompt summary_bytes=2145
extraction_retry_success agent=code_reviewer findings=2
```

Agent completion and context enrichment:

```
agent_completed agent=code_reviewer findings=3 duration=4.23s status=success
context_enriched agent=code_reviewer prior_context_bytes=0 enrichment_bytes=847 total_bytes=847
```

The next agent in the sequence sees the enriched context:

```
agent_start agent=security_analyst diff_size_kb=12 context_bytes=847
```

## Parallel Mode

When running agents in parallel (e.g., `security_focus`), they start simultaneously without context enrichment:

```
agents_built count=2 execution_mode=parallel
agent_start agent=security_analyst diff_size_kb=12 context_bytes=0
agent_start agent=performance_analyst diff_size_kb=12 context_bytes=0
```

Both run concurrently; results are merged when all complete.

## Large Diff Chunking

If your diff exceeds `diff_chunk_size_kb` (default 25 KB):

```
diff chunking enabled total_size_kb=145 num_chunks=6 chunk_size_kb=25 overlap_kb=2
agent_start agent=code_reviewer diff_size_kb=25 context_bytes=0
chunk_run agent=code_reviewer chunk=1/6 size_kb=25
chunk_complete agent=code_reviewer chunk=1 findings=2 duration=4.56s
chunk_run agent=code_reviewer chunk=2/6 size_kb=25
chunk_complete agent=code_reviewer chunk=2 findings=1 duration=3.21s
...
chunk_merge agent=code_reviewer total_findings=8 final_status=success
```

## Deduplication

After all agents finish, findings are deduped across agents:

```
dedup: dropped duplicate finding title='hardcoded password' from security_analyst
dedup_agent agent=security_analyst original_findings=5 unique_findings=4 dropped=1
dedup_agent agent=code_reviewer original_findings=3 unique_findings=2 dropped=1
dedup_summary total_dropped=2 unique_kept=8
```

## Workflow Completion

Final summary:

```
workflow=full_review done duration=22.5s total_findings=8
```

## Error & Timeout Handling

Agent timeout:

```
agent_timeout agent=code_reviewer timeout_sec=180 elapsed_sec=180.00
```

Agent error:

```
agent_error agent=security_analyst elapsed_sec=15.23 error=RuntimeError('git show failed')
```

Repo not found:

```
tool_git_error tool=fetch_diff error=repo_not_found repo_url=/nonexistent/path
```

## Key Log Fields

Use these to grep/filter logs:

| Field | Meaning |
|-------|---------|
| `repo=` | Repository name |
| `branch=` | Git branch |
| `run_id=` | Unique run identifier |
| `agent=` | Agent name (code_reviewer, security_analyst, etc.) |
| `turn=` | ReAct loop iteration (1, 2, 3, ...) |
| `findings=` | Number of findings returned |
| `duration=` | Time elapsed in seconds |
| `status=` | success, timeout, or error |
| `chunk=` | For chunked diffs: "1/6", "2/6", etc. |
| `tool=` | Tool name (fetch_git_diff, get_file_content, etc.) |
| `mode=` | sequential or parallel execution |

## Example: Full Run Tail

```sh
tail -f asdlc.log | jq 'select(.agent == "code_reviewer" or .repo == "inventory-tracker")'
```

This shows all logs mentioning either the code_reviewer agent or the inventory-tracker repo, useful for focusing on one workflow run.

## Troubleshooting with Logs

**Agent produces 0 findings despite detailed prose:**
```
extraction_success agent=code_reviewer findings=0
extraction returned 0 findings, retrying...
extraction_retry_success agent=code_reviewer findings=2
```
→ Extraction retry kicked in and recovered findings.

**Agent times out on large diff:**
```
diff chunking enabled total_size_kb=250 num_chunks=10 chunk_size_kb=25
agent_timeout agent=code_reviewer timeout_sec=180 elapsed_sec=180.00
```
→ Consider raising `AGENT_TIMEOUT_SECONDS` or `DIFF_CHUNK_SIZE_KB` (be careful with chunk size).

**Model is slow on a specific repo:**
```
agent_start agent=security_analyst diff_size_kb=45 context_bytes=1200
agent_completed agent=security_analyst findings=2 duration=45.67s status=success
```
→ Security analyst took 45s on a 45 KB diff. Check Ollama performance or consider a faster model.

**Deduplication removing too many findings:**
```
dedup_agent agent=code_reviewer original_findings=10 unique_findings=2 dropped=8
```
→ Many duplicates across agents. May indicate agents are hallucinating common findings; check `extraction_success` severity distribution.
