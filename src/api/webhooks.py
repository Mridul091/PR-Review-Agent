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

from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.api.middleware import verify_github_signature
from src.api.review import build_review_result, get_review_store
from src.review.schemas import ReviewRequest, ReviewStatus
from src.review.service import ReviewService
from src.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["Webhooks"])

# ── Actions that should trigger a full review ────────────────────────────────
REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened"}


class _RepositoryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    full_name: Annotated[str, Field(pattern=r"^[^/\s]+/[^/\s]+$")]


class _CommitRefPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sha: str


class _PullRequestPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    number: Annotated[int, Field(gt=0)]
    base: _CommitRefPayload
    head: _CommitRefPayload


class _InstallationPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Annotated[int, Field(gt=0)]


class _ReviewableWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: str
    repository: _RepositoryPayload
    pull_request: _PullRequestPayload
    installation: _InstallationPayload


def get_review_service(request: Request) -> ReviewService:
    app = cast(FastAPI, request.app)
    return cast(ReviewService, app.state.review_service)


def build_review_request_from_webhook(payload: _ReviewableWebhookPayload) -> ReviewRequest:
    return ReviewRequest(
        repository=payload.repository.full_name,
        installation_id=payload.installation.id,
        pull_request_number=payload.pull_request.number,
        base_sha=payload.pull_request.base.sha,
        head_sha=payload.pull_request.head.sha,
    )


async def _run_pr_review(
    review_request: ReviewRequest,
    action: str,
    service: ReviewService,
    request: Request,
    delivery_id: str,
) -> None:
    """Run a review, persist its result, and move the delivery's claim to its end state."""
    store = get_review_store(request)
    await store.update_claim_status(delivery_id, "running")
    logger.info(
        "pr_review_started",
        repo=review_request.repository,
        pr_number=review_request.pull_request_number,
        gh_action=action,
        base_sha=review_request.base_sha,
        head_sha=review_request.head_sha,
        installation_id=review_request.installation_id,
    )

    try:
        run = await service.review(review_request)
        result = build_review_result(run)
        await store.save_result(result)
    except Exception:
        logger.exception(
            "pr_review_failed_unexpectedly",
            repo=review_request.repository,
            pr_number=review_request.pull_request_number,
        )
        # Release the claim so a redelivery can retry this commit.
        await store.update_claim_status(delivery_id, "failed")
        return

    # Only a failed review releases the claim; partial and rejected results are final.
    await store.update_claim_status(
        delivery_id, "failed" if result.status == ReviewStatus.FAILED else "completed"
    )

    logger.info(
        "pr_review_finished",
        repo=review_request.repository,
        pr_number=review_request.pull_request_number,
        review_id=result.review_id,
        review_status=result.status.value,
        findings_count=len(result.findings),
        failure_count=len(result.failures),
    )


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
    service: Annotated[ReviewService, Depends(get_review_service)],
    _: None = Depends(verify_github_signature),
) -> dict:
    """
    Webhook entry point.

    Returns 202 immediately so GitHub does not retry.
    Heavy work happens in _run_pr_review as a BackgroundTask.
    """
    event_type = request.headers.get("X-GitHub-Event")
    delivery_id = request.headers.get("X-GitHub-Delivery")
    try:
        payload: dict = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Webhook body must be a JSON object.")
    action: str = payload.get("action", "")

    logger.info(
        "webhook_received",
        gh_event=event_type,
        gh_action=action,
        delivery_id=delivery_id,
    )

    # ── Dispatch pull_request events ─────────────────────────────────────────
    if event_type == "pull_request" and action in REVIEWABLE_ACTIONS:
        try:
            validated = _ReviewableWebhookPayload.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail="Reviewable pull_request payload is missing required fields.",
            ) from exc
        review_request = build_review_request_from_webhook(validated)
        repo = review_request.repository
        pr_number = review_request.pull_request_number

        logger.info(
            "webhook_dispatching_review",
            repo=repo,
            pr_number=pr_number,
            gh_action=action,
        )

        if not delivery_id:
            raise HTTPException(status_code=400, detail="Delivery id should be valid")
        store = get_review_store(request)
        won = await store.claim_review(
            delivery_id,
            installation_id=review_request.installation_id,
            repository=review_request.repository,
            pull_request_number=review_request.pull_request_number,
            head_sha=review_request.head_sha,
        )

        if won is False:
            logger.info(
                "webhook_duplicate",
                delivery_id=delivery_id,
                pr_number=pr_number,
                head_sha=review_request.head_sha,
            )
            return {
                "status": "duplicate",
                "event": event_type,
                "action": action,
                "repo": repo,
                "pr_number": pr_number,
                "message": f"Review job rejected because of duplicate PR #{pr_number} on {repo}.",
            }
        background_tasks.add_task(
            _run_pr_review, review_request, action, service, request, delivery_id
        )

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
