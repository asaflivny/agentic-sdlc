from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5-coder:7b"

    # Per-agent model overrides (fall back to ollama_model if empty)
    code_review_model: str = ""
    security_model: str = ""
    performance_model: str = ""
    dep_audit_model: str = ""
    test_coverage_model: str = ""

    agent_timeout_seconds: int = 180
    max_tokens: int = 4096
    max_concurrent_runs: int = 3

    # LangGraph agent loop
    agent_recursion_limit: int = 25  # super-steps per agent subgraph (call_model<->tools)
    agent_use_tools: bool = False  # enable the ReAct tool loop (needs a tool-reliable model);
    # off = analyze the pre-fetched diff directly (recommended for small local models)
    enable_checkpointing: bool = (
        True  # persist graph state for resumable runs; allows recovery from server restarts
    )
    checkpoint_db_path: str = "asdlc_checkpoints.db"

    extraction_retry: bool = (
        True  # retry structured extraction once when 0 findings but prose exists
    )
    diff_chunk_size_kb: int = (
        25  # split diffs larger than this into overlapping chunks; 0 to disable
    )

    github_token: str = ""  # post findings as PR review comments when set
    github_repo: str = ""  # owner/repo — auto-detected from push payload if omitted

    webhook_secret: str = ""
    result_webhook_url: str = ""
    slack_webhook_url: str = ""  # Slack webhook URL for notifications
    slack_mention_channels: str = ""  # Slack channels/users to mention (comma-separated)
    email_webhook_url: str = ""  # Email service webhook (e.g. Sendgrid, Mailgun)
    email_recipients: str = ""  # Email recipients (comma-separated)
    jenkins_default_api_token: str = ""  # Default Jenkins API token for all builds
    api_key: str = ""
    rate_limit_per_repo: int = 10  # max pushes per repo per minute

    db_path: str = "asdlc.db"
    log_level: str = "INFO"

    # RAG Configuration
    rag_enabled: bool = True
    rag_db_path: str = "asdlc_rag.db"  # Chroma persistent directory
    rag_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # HuggingFace embeddings
    rag_chunk_size: int = 500  # Characters per chunk when indexing files
    rag_similarity_threshold: float = 0.75  # For finding deduplication
    rag_search_limit: int = 5  # Default results per query

    # Repository management
    repos_root: str = "/repos"  # Local directory where repos are cloned
    git_clone_sources: str = ""  # Comma-separated list of repos to clone on startup
    # Format: "repo_name:source_path" e.g. "inventory-tracker:/local/git/inventory-tracker"

    @property
    def ollama_native_url(self) -> str:
        """ChatOllama uses the native Ollama API; strip the OpenAI-compat /v1 suffix."""
        url = self.ollama_base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        return url

    def model_for_agent(self, agent_name: str) -> str:
        overrides = {
            "code_reviewer": self.code_review_model,
            "security_analyst": self.security_model,
            "performance_analyst": self.performance_model,
            "dep_auditor": self.dep_audit_model,
            "test_coverage": self.test_coverage_model,
        }
        return overrides.get(agent_name, "") or self.ollama_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
