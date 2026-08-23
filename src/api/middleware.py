"""
Middleware: GitHub Webhook Signature Verification.

GitHub signs every webhook payload with HMAC-SHA256 using the webhook secret
configured in your GitHub App. We verify this signature on every request to
ensure the payload genuinely comes from GitHub and has not been tampered with.

Reference: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
"""
import hashlib
import hmac

from fastapi import HTTPException, Request

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def verify_github_signature(request: Request) -> None:
    """
    FastAPI dependency that validates the X-Hub-Signature-256 header.

    Raises:
        HTTPException 401 — if header is missing or signature does not match.
        HTTPException 400 — if signature algorithm is not sha256.
    """
    # If no secret is configured (local dev without webhook tunnel), skip check
    if not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning(
            "webhook_signature_skipped",
            reason="GITHUB_WEBHOOK_SECRET not configured",
        )
        return

    header_value = request.headers.get("X-Hub-Signature-256")
    if not header_value:
        logger.error("webhook_missing_signature_header")
        raise HTTPException(status_code=401, detail="X-Hub-Signature-256 header is required.")

    try:
        algo, provided_sig = header_value.split("=", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed X-Hub-Signature-256 header.")

    if algo != "sha256":
        raise HTTPException(status_code=400, detail=f"Unsupported signature algorithm: {algo}")

    body = await request.body()
    expected_sig = hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, provided_sig):
        logger.error("webhook_signature_mismatch")
        raise HTTPException(status_code=401, detail="Webhook signature verification failed.")

    logger.debug("webhook_signature_verified")
