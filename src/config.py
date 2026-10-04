"""Application settings loaded from environment variables via Pydantic Settings."""

import re
from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central config object.
    All values can be overridden via environment variables or a .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ─────────────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    REVIEW_API_TOKEN: Optional[str] = None
    # Installation IDs mapped to target repositories permitted for the API token.
    REVIEW_REPOSITORY_ACCESS: dict[int, list[str]] = Field(default_factory=dict)

    # ── GitHub App ─────────────────────────────────────────────────────────────
    GITHUB_APP_ID: Optional[str] = None
    GITHUB_PRIVATE_KEY_PATH: Optional[str] = None
    GITHUB_WEBHOOK_SECRET: Optional[str] = None

    # ── Input safety limits ────────────────────────────────────────────────────
    MAX_PR_FILES: int = 300
    MAX_DIFF_BYTES: int = 1_000_000
    MAX_REVIEW_CONTEXT_CHARS: int = 20_000
    MAX_MODEL_ATTEMPTS: int = 2
    MAX_PATCH_BYTES: int = 250_000
    MAX_WEBHOOK_BYTES: int = 1_000_000
    GITHUB_REQUEST_TIMEOUT_SECONDS: float = 30.0

    # ── LLM ────────────────────────────────────────────────────────────────────
    LLM_PROVIDER: Literal["fake", "groq"] = "fake"
    LLM_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None

    # ── RAG (Phase 5) ──────────────────────────────────────────────────────────
    CHROMA_PERSIST_DIR: str = "./chroma_db"
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"

    # ── Database (Phase 6) ─────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./pr_reviewer.db"

    @model_validator(mode="after")
    def validate_security_and_limits(self) -> "Settings":
        if self.ENVIRONMENT == "production" and not self.GITHUB_WEBHOOK_SECRET:
            raise ValueError("GITHUB_WEBHOOK_SECRET is required in production")
        if self.ENVIRONMENT == "production" and not self.REVIEW_API_TOKEN:
            raise ValueError("REVIEW_API_TOKEN is required in production")
        if self.ENVIRONMENT == "production" and self.LLM_PROVIDER == "fake":
            raise ValueError("LLM_PROVIDER=fake is not allowed in production")
        for installation_id, repositories in self.REVIEW_REPOSITORY_ACCESS.items():
            if installation_id <= 0:
                raise ValueError("REVIEW_REPOSITORY_ACCESS requires positive installation IDs")
            for repository in repositories:
                if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
                    raise ValueError("REVIEW_REPOSITORY_ACCESS requires owner/repo names")
        limits = {
            "MAX_PR_FILES": self.MAX_PR_FILES,
            "MAX_DIFF_BYTES": self.MAX_DIFF_BYTES,
            "MAX_REVIEW_CONTEXT_CHARS": self.MAX_REVIEW_CONTEXT_CHARS,
            "MAX_MODEL_ATTEMPTS": self.MAX_MODEL_ATTEMPTS,
            "MAX_PATCH_BYTES": self.MAX_PATCH_BYTES,
            "MAX_WEBHOOK_BYTES": self.MAX_WEBHOOK_BYTES,
            "GITHUB_REQUEST_TIMEOUT_SECONDS": self.GITHUB_REQUEST_TIMEOUT_SECONDS,
        }
        invalid = [name for name, value in limits.items() if value <= 0]
        if invalid:
            raise ValueError(f"Input limits must be positive: {', '.join(invalid)}")
        if self.MAX_REVIEW_CONTEXT_CHARS > 1_000_000:
            raise ValueError("MAX_REVIEW_CONTEXT_CHARS cannot exceed 1,000,000")
        if self.MAX_MODEL_ATTEMPTS > 3:
            raise ValueError("MAX_MODEL_ATTEMPTS cannot exceed 3")
        return self


# Singleton instance — import this everywhere
settings = Settings()
