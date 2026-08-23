"""
GitHub App Authentication.

GitHub Apps use a 2-step token exchange:
  1. Sign a JWT with your App's RSA private key  (valid 10 min)
  2. POST that JWT to GitHub to get an Installation Access Token (valid 1 hour)

The Installation Token is what you use for all GitHub API calls.

Usage:
    auth = GitHubAppAuth()
    token = await auth.get_installation_token(installation_id=12345678)
    headers = auth.get_auth_headers(token)

Reference:
  https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
import jwt

from src.config import settings
from src.utils.logging import get_logger

# GitHub API base URL
GITHUB_API_URL = "https://api.github.com"


@dataclass(frozen=True)
class _CachedInstallationToken:
    token: str
    expires_at: datetime


class GitHubAppAuth:
    """
    Handles all GitHub App authentication concerns:
      - Loading the RSA private key
      - Generating short-lived JWTs
      - Exchanging JWTs for Installation Access Tokens
      - Caching tokens so we don't regenerate on every API call

    Each instance maintains its own token cache, so you can create
    a single instance and reuse it across the application lifetime.
    """

    def __init__(self) -> None:
        self._logger = get_logger(__name__)
        self._token_cache: dict[int, _CachedInstallationToken] = {}
        self._private_key: Optional[str] = None  # Lazy-loaded on first use

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_private_key(self) -> str:
        """
        Load the RSA private key from disk (lazy, cached after first read).

        Raises:
            ValueError: If GITHUB_PRIVATE_KEY_PATH is not configured.
            FileNotFoundError: If the .pem file doesn't exist at the given path.
        """
        if self._private_key:
            return self._private_key

        key_path = settings.GITHUB_PRIVATE_KEY_PATH
        if not key_path:
            raise ValueError(
                "GITHUB_PRIVATE_KEY_PATH is not set. "
                "Add it to your .env file pointing to your GitHub App .pem file."
            )
        try:
            with open(key_path, "r") as f:
                self._private_key = f.read()
                return self._private_key
        except FileNotFoundError:
            raise FileNotFoundError(
                f"GitHub App private key not found at: {key_path}\n"
                "Download it from: GitHub → Settings → Developer Settings → "
                "GitHub Apps → Your App → Private keys → Generate a private key."
            )

    def _get_cached_token(self, installation_id: int) -> Optional[_CachedInstallationToken]:
        """
        Return a cached installation token when it has more than five minutes
        remaining, otherwise remove the stale entry.
        """
        cached = self._token_cache.get(installation_id)
        if cached is None:
            return None

        remaining = (cached.expires_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 300:
            self._token_cache.pop(installation_id, None)
            return None
        return cached

    # ── Public methods ────────────────────────────────────────────────────────

    def generate_jwt(self) -> str:
        """
        Generate a short-lived JWT signed with the GitHub App's RSA private key.

        The JWT is valid for 10 minutes and is only used to obtain an
        Installation Access Token — it cannot call the GitHub API directly.

        Returns:
            A signed JWT string.

        Raises:
            ValueError: If GITHUB_APP_ID is not set.
        """
        if not settings.GITHUB_APP_ID:
            raise ValueError("GITHUB_APP_ID is not set in your .env file.")

        private_key = self._load_private_key()
        now = int(time.time())

        payload = {
            "iat": now - 60,          # Issued 60s ago (handles clock skew)
            "exp": now + (10 * 60),   # Expires in 10 minutes
            "iss": settings.GITHUB_APP_ID,
        }

        token = jwt.encode(payload, private_key, algorithm="RS256")
        self._logger.debug("github_jwt_generated", app_id=settings.GITHUB_APP_ID)
        return token

    async def get_installation_token(self, installation_id: int) -> str:
        """
        Exchange a JWT for a GitHub Installation Access Token.

        Installation tokens:
        - Are scoped to a single installation (one account/org)
        - Expire after 1 hour
        - Are cached in memory and reused until 5 minutes before expiry

        Args:
            installation_id: The GitHub App installation ID.
                             Found in the webhook payload under installation.id

        Returns:
            A short-lived GitHub API token string (e.g. "ghs_abc123...").
        """
        # Installation tokens are scoped to one installation. Cache lookups must
        # therefore include the installation ID to prevent cross-account access.
        cached = self._get_cached_token(installation_id)
        if cached is not None:
            remaining = (cached.expires_at - datetime.now(timezone.utc)).total_seconds()
            self._logger.debug(
                "github_token_cache_hit",
                installation_id=installation_id,
                expires_in_seconds=int(remaining),
            )
            return cached.token

        # Generate a fresh JWT and exchange it for an installation token
        jwt_token = self.generate_jwt()

        url = f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers)

        if response.status_code != 201:
            self._logger.error(
                "github_token_exchange_failed",
                status_code=response.status_code,
                body=response.text,
            )
            response.raise_for_status()

        data = response.json()

        # Cache the token and its expiry
        expires_at = datetime.fromisoformat(
            data["expires_at"].replace("Z", "+00:00")
        )
        self._token_cache[installation_id] = _CachedInstallationToken(
            token=data["token"],
            expires_at=expires_at,
        )

        self._logger.info(
            "github_token_obtained",
            installation_id=installation_id,
            expires_at=data["expires_at"],
        )
        return data["token"]

    def get_auth_headers(self, token: str) -> dict:
        """
        Build the standard GitHub API authorization headers for a given token.

        Args:
            token: A GitHub Installation Access Token.

        Returns:
            A dict of HTTP headers ready to pass to any GitHub API request.
        """
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def clear_cache(self, installation_id: Optional[int] = None) -> None:
        """
        Invalidate one installation token, or all cached tokens when no
        installation ID is provided.

        Useful in tests or when you need to force a token refresh.
        """
        if installation_id is None:
            self._token_cache.clear()
        else:
            self._token_cache.pop(installation_id, None)
        self._logger.debug(
            "github_token_cache_cleared",
            installation_id=installation_id,
        )
