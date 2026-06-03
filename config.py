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
    enable_checkpointing: bool = False  # persist graph state for resumable runs
    checkpoint_db_path: str = "asdlc_checkpoints.db"

    webhook_secret: str = ""
    result_webhook_url: str = ""
    slack_webhook_url: str = ""
    api_key: str = ""
    rate_limit_per_repo: int = 10  # max pushes per repo per minute

    db_path: str = "asdlc.db"
    log_level: str = "INFO"

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
