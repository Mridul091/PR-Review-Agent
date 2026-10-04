"""
FastAPI application entry point.

Start the server:
    make dev
    # or directly:
    uvicorn src.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.review import router as review_router
from src.api.webhooks import router as webhook_router
from src.config import settings
from src.github.auth import GitHubAppAuth
from src.github.client import GitHubClient
from src.llm.factory import create_llm_provider
from src.middleware.auth import AuthenticationMiddleware
from src.review.service import ReviewService
from src.review.store import SQLiteReviewStore
from src.utils.logging import get_logger, setup_logging


# ── Lifespan (startup / shutdown) ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs setup code before the server starts accepting requests,
    and teardown code when it shuts down.
    """
    setup_logging(log_level=settings.LOG_LEVEL, environment=settings.ENVIRONMENT)
    logger = get_logger(__name__)
    app.state.review_api_token = settings.REVIEW_API_TOKEN
    app.state.review_allowed_repositories = frozenset(
        (installation_id, repository.casefold())
        for installation_id, repositories in settings.REVIEW_REPOSITORY_ACCESS.items()
        for repository in repositories
    )
    github_auth = GitHubAppAuth()
    github_client = GitHubClient(github_auth)
    llm_provider = create_llm_provider(settings)

    app.state.review_service = ReviewService(
        auth=github_auth,
        github_client=github_client,
        llm_provider=llm_provider,
        model_name=settings.LLM_MODEL,
        max_context_chars=settings.MAX_REVIEW_CONTEXT_CHARS,
        max_model_attempts=settings.MAX_MODEL_ATTEMPTS,
    )

    store = SQLiteReviewStore(settings.DATABASE_URL)

    await store.initialize()
    app.state.review_store = store

    logger.info(
        "server_starting",
        environment=settings.ENVIRONMENT,
        llm_provider=settings.LLM_PROVIDER,
        llm_model=settings.LLM_MODEL,
        host=settings.HOST,
        port=settings.PORT,
    )

    # Future startup hooks (added in later phases):
    #   Phase 5 → initialise ChromaDB vector store
    #   Phase 6 → run Alembic DB migrations

    yield

    close_provider = getattr(llm_provider, "aclose", None)
    if close_provider is not None:
        await close_provider()
    logger.info("server_stopped")


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="AI PR Reviewer Agent",
    description=(
        "A production-grade, multi-agent system that automatically reviews "
        "GitHub Pull Requests using LangGraph orchestration and RAG memory."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow the Next.js dashboard on any port during development
app.add_middleware(AuthenticationMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.ENVIRONMENT == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(webhook_router, prefix="/api/v1")
app.include_router(review_router, prefix="/api/v1")


# ── Built-in endpoints ────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"], summary="Health check")
async def health_check() -> dict:
    """Returns the current server health status."""
    return {
        "status": "healthy",
        "service": "pr-reviewer-agent",
        "version": "0.1.0",
        "environment": settings.ENVIRONMENT,
        "llm_model": settings.LLM_MODEL,
    }


# ── Direct run ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.ENVIRONMENT == "development",
    )
