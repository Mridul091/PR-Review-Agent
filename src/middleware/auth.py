"""Bearer authentication and server-assigned repository permissions."""

import hmac
from dataclasses import dataclass
from typing import cast

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class ReviewPrincipal:
    subject: str
    allowed_repositories: frozenset[tuple[int, str]]


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Authenticate callers of protected review API routes."""

    _public_paths = {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/webhook",
    }

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self._public_paths:
            return await call_next(request)

        if not (
            request.url.path == "/api/v1/reviews" or request.url.path.startswith("/api/v1/reviews/")
        ):
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")

        expected_token = getattr(request.app.state, "review_api_token", None)

        if (
            scheme.lower() != "bearer"
            or not token
            or not expected_token
            or not hmac.compare_digest(token, expected_token)
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing bearer token."},
            )

        allowed = cast(
            frozenset[tuple[int, str]],
            getattr(request.app.state, "review_allowed_repositories", frozenset()),
        )
        request.state.principal = ReviewPrincipal(
            subject="review-api-client", allowed_repositories=allowed
        )
        return await call_next(request)
