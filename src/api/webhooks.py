"""
GitHub Webhook Endpoint.

Receives events from GitHub, validates the signature, then dispatches
eligible pull_request events to a background task for async processing.

Supported events:
  - pull_request.opened
  - pull_request.synchronize  (new commit pushed to existing PR)
  - pull_request.reopened

All other events and actions are acknowledged but ignored (200 + status:ignored).
"""
from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from src.api.middleware import verify_github_signature
from src.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["Webhooks"])

# ── Actions that should trigger a full review ────────────────────────────────
REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened"}


# ── Background task (stub — filled in Phase 4) ───────────────────────────────
async def _run_pr_review(payload: dict) -> None:
    """
    Orchestrates the full multi-agent PR review pipeline.
    Runs as a FastAPI BackgroundTask so the webhook endpoint returns instantly.

    Phases that will populate this function:
      Phase 2 → GitHub client fetches diff
      Phase 3 → LLM provider initialised
      Phase 4 → LangGraph orchestrator invoked
      Phase 5 → RAG context retrieved & findings stored
    """
    repo = payload.get("repository", {}).get("full_name", "unknown/repo")
    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")
    action = payload.get("action")

    logger.info(
        "pr_review_started",
        repo=repo,
        pr_number=pr_number,
        gh_action=action,
    )

    # TODO (Phase 4): invoke orchestrator
    # result = await orchestrator.run(payload)
    # TODO (Phase 5): store findings in RAG memory
    # await memory.store_findings(result)

    logger.info("pr_review_stub_complete", repo=repo, pr_number=pr_number)


# ── Endpoint ─────────────────────────────────────────────────────────────────
@router.post(
    "/webhook",
    status_code=status.HTTP_202_ACCEPTED,
    summary="GitHub Webhook Receiver",
    description=(
        "Receives GitHub webhook events, verifies the payload signature, "
        "and enqueues PR review tasks for background processing."
    ),
)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_github_signature),
) -> dict:
    """
    Webhook entry point.

    Returns 202 immediately so GitHub does not retry.
    Heavy work happens in _run_pr_review as a BackgroundTask.
    """
    event_type = request.headers.get("X-GitHub-Event", "unknown")
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")
    payload: dict = await request.json()
    action: str = payload.get("action", "")

    logger.info(
        "webhook_received",
        gh_event=event_type,
        gh_action=action,
        delivery_id=delivery_id,
    )

    # ── Dispatch pull_request events ─────────────────────────────────────────
    if event_type == "pull_request" and action in REVIEWABLE_ACTIONS:
        repo = payload.get("repository", {}).get("full_name", "unknown")
        pr_number = payload.get("pull_request", {}).get("number")

        logger.info(
            "webhook_dispatching_review",
            repo=repo,
            pr_number=pr_number,
            gh_action=action,
        )
        background_tasks.add_task(_run_pr_review, payload)

        return {
            "status": "accepted",
            "event": event_type,
            "action": action,
            "repo": repo,
            "pr_number": pr_number,
            "message": f"Review job enqueued for PR #{pr_number} on {repo}.",
        }

    # ── Ignore all other events ──────────────────────────────────────────────
    logger.debug("webhook_ignored", gh_event=event_type, gh_action=action)
    return {
        "status": "ignored",
        "event": event_type,
        "action": action,
        "message": f"Event '{event_type}/{action}' is not handled.",
    }
