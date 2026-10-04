"""
GitHub API Client.

Wraps all GitHub REST API calls the PR Reviewer Agent needs:
  - Fetching PR metadata, diff, and changed files
  - Posting inline review comments
  - Posting summary comments
  - Setting commit check status

All methods are async (uses httpx.AsyncClient) and automatically
handle authentication via GitHubAppAuth.

Usage:
    auth = GitHubAppAuth()
    client = GitHubClient(auth)

    token = await auth.get_installation_token(installation_id)
    pr    = await client.get_pull_request("owner/repo", pr_number, token)
    diff  = await client.get_pr_diff("owner/repo", pr_number, token)

Reference:
  https://docs.github.com/en/rest/pulls
"""

from dataclasses import dataclass
from typing import Literal, Optional

import httpx

from src.config import settings
from src.github.auth import GitHubAppAuth
from src.github.diff_parser import FileDiff, parse_diff
from src.github.errors import GitHubClientError, InputLimitExceededError
from src.utils.logging import get_logger

GITHUB_API_URL = "https://api.github.com"


# ── Response models ───────────────────────────────────────────────────────────


@dataclass
class PRAuthor:
    """The GitHub user who opened the PR."""

    login: str
    avatar_url: str
    html_url: str


@dataclass
class PRMetadata:
    """All key metadata about a Pull Request."""

    number: int
    title: str
    body: str
    state: str  # "open" | "closed" | "merged"
    author: PRAuthor
    base_branch: str  # Branch being merged INTO (e.g. "main")
    base_sha: str
    head_branch: str  # Branch being merged FROM (e.g. "feat/login")
    head_sha: str  # Latest commit SHA on the PR branch
    target_repo_full_name: str  # Repository receiving the PR
    head_repo_full_name: Optional[str]  # Source repository; absent if deleted
    html_url: str  # Link to the PR on GitHub
    draft: bool
    changed_files: int
    additions: int
    deletions: int

    @property
    def repo_full_name(self) -> str:
        """Backward-compatible alias for the target repository."""
        return self.target_repo_full_name


@dataclass
class ReviewComment:
    """
    A single inline comment to post on a specific line of a file.
    Used when calling post_review().
    """

    path: str  # File path, e.g. "src/auth.py"
    line: int  # Line number in the NEW file to attach the comment to
    body: str  # The comment text (supports Markdown)
    side: str = "RIGHT"  # "RIGHT" = new file, "LEFT" = old file


@dataclass
class PRFile:
    """Metadata about a single changed file in a PR."""

    filename: str
    status: str  # "added" | "modified" | "removed" | "renamed"
    additions: int
    deletions: int
    patch: Optional[str] = None  # Raw diff patch (None for binary files)
    previous_filename: Optional[str] = None
    patch_status: Literal["available", "unavailable"] = "unavailable"


# ── Client ────────────────────────────────────────────────────────────────────


class GitHubClient:
    """
    Async client for all GitHub API interactions needed by the PR Reviewer.

    Instantiate once and reuse — it holds no mutable state beyond the
    auth reference, so it is safe to share across requests.
    """

    def __init__(self, auth: GitHubAppAuth) -> None:
        self._auth = auth
        self._logger = get_logger(__name__)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _headers(self, token: str) -> dict:
        """Standard GitHub API headers for authenticated requests."""
        return self._auth.get_auth_headers(token)

    async def _get(self, url: str, token: str, **kwargs) -> httpx.Response:
        """Execute an authenticated GET request and raise on HTTP errors."""
        try:
            async with httpx.AsyncClient(timeout=settings.GITHUB_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers=self._headers(token), **kwargs)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GitHubClientError(
                code="github_timeout",
                message="GitHub did not respond before the request timeout.",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise self._http_error(exc) from exc
        return response

    async def _post(self, url: str, token: str, json: dict) -> httpx.Response:
        """Execute an authenticated POST request and raise on HTTP errors."""
        try:
            async with httpx.AsyncClient(timeout=settings.GITHUB_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(url, headers=self._headers(token), json=json)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GitHubClientError(
                code="github_timeout",
                message="GitHub did not respond before the request timeout.",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise self._http_error(exc) from exc
        return response

    @staticmethod
    def _http_error(exc: httpx.HTTPStatusError) -> GitHubClientError:
        status_code = exc.response.status_code
        return GitHubClientError(
            code="github_http_error",
            message=f"GitHub API request failed with status {status_code}.",
            retryable=status_code == 429 or status_code >= 500,
            status_code=status_code,
        )

    def _repo_url(self, repo: str) -> str:
        """Base URL for a given repo, e.g. 'owner/repo'."""
        return f"{GITHUB_API_URL}/repos/{repo}"

    # ── Pull Request ──────────────────────────────────────────────────────────

    async def get_pull_request(self, repo: str, pr_number: int, token: str) -> PRMetadata:
        """
        Fetch full metadata for a single Pull Request.

        Args:
            repo:      Repository full name, e.g. "octocat/hello-world"
            pr_number: The PR number.
            token:     GitHub Installation Access Token.

        Returns:
            A PRMetadata dataclass with all key PR fields.
        """
        url = f"{self._repo_url(repo)}/pulls/{pr_number}"
        self._logger.info("fetching_pr_metadata", repo=repo, pr_number=pr_number)

        response = await self._get(url, token)
        data = response.json()
        target_repo = data["base"]["repo"]["full_name"]
        if target_repo.casefold() != repo.casefold():
            raise GitHubClientError(
                code="repository_identity_mismatch",
                message="GitHub returned pull request data for a different target repository.",
            )

        return PRMetadata(
            number=data["number"],
            title=data["title"],
            body=data.get("body") or "",
            state=data["state"],
            author=PRAuthor(
                login=data["user"]["login"],
                avatar_url=data["user"]["avatar_url"],
                html_url=data["user"]["html_url"],
            ),
            base_branch=data["base"]["ref"],
            base_sha=data["base"]["sha"],
            head_branch=data["head"]["ref"],
            head_sha=data["head"]["sha"],
            target_repo_full_name=target_repo,
            head_repo_full_name=(data["head"].get("repo") or {}).get("full_name"),
            html_url=data["html_url"],
            draft=data.get("draft", False),
            changed_files=data.get("changed_files", 0),
            additions=data.get("additions", 0),
            deletions=data.get("deletions", 0),
        )

    async def get_pr_diff(self, repo: str, pr_number: int, token: str) -> list[FileDiff]:
        """
        Fetch and parse the unified diff of a PR into FileDiff objects.

        The raw diff is fetched using GitHub's diff media type, then
        passed through the UnifiedDiffParser.

        Args:
            repo:      Repository full name.
            pr_number: The PR number.
            token:     GitHub Installation Access Token.

        Returns:
            A list of FileDiff objects — one per changed file.
        """
        url = f"{self._repo_url(repo)}/pulls/{pr_number}"
        self._logger.info("fetching_pr_diff", repo=repo, pr_number=pr_number)

        # Request the diff format using GitHub's diff media type
        try:
            async with httpx.AsyncClient(timeout=settings.GITHUB_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    url,
                    headers={
                        **self._headers(token),
                        "Accept": "application/vnd.github.diff",
                    },
                )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GitHubClientError(
                code="github_timeout",
                message="GitHub did not respond before the request timeout.",
                retryable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise self._http_error(exc) from exc

        raw_diff = response.text
        diff_bytes = len(response.content)
        if diff_bytes > settings.MAX_DIFF_BYTES:
            raise InputLimitExceededError(
                code="diff_too_large",
                message=(
                    f"Pull request diff is {diff_bytes} bytes; "
                    f"the configured limit is {settings.MAX_DIFF_BYTES} bytes."
                ),
            )
        files = parse_diff(raw_diff)
        if len(files) > settings.MAX_PR_FILES:
            raise InputLimitExceededError(
                code="too_many_files",
                message=(
                    f"Pull request contains {len(files)} files; "
                    f"the configured limit is {settings.MAX_PR_FILES}."
                ),
            )

        self._logger.info(
            "pr_diff_parsed",
            repo=repo,
            pr_number=pr_number,
            total_files=len(files),
            binary_files=sum(1 for f in files if f.is_binary),
        )
        return files

    async def get_pr_files(self, repo: str, pr_number: int, token: str) -> list[PRFile]:
        """
        Fetch the list of files changed in a PR (metadata only, no diff).

        Useful for a quick overview of what changed before fetching the
        full diff.

        Args:
            repo:      Repository full name.
            pr_number: The PR number.
            token:     GitHub Installation Access Token.

        Returns:
            A list of PRFile objects.
        """
        url = f"{self._repo_url(repo)}/pulls/{pr_number}/files"
        self._logger.info("fetching_pr_files", repo=repo, pr_number=pr_number)

        files: list[PRFile] = []
        page = 1
        total_patch_bytes = 0
        while True:
            response = await self._get(
                url,
                token,
                params={"per_page": 100, "page": page},
            )
            items = response.json()
            for item in items:
                if len(files) >= settings.MAX_PR_FILES:
                    raise InputLimitExceededError(
                        code="too_many_files",
                        message=f"Pull request exceeds the {settings.MAX_PR_FILES}-file limit.",
                    )
                patch = item.get("patch")
                patch_bytes = len(patch.encode("utf-8")) if patch is not None else 0
                if patch_bytes > settings.MAX_PATCH_BYTES:
                    raise InputLimitExceededError(
                        code="patch_too_large",
                        message=(
                            f"Patch for {item['filename']} is {patch_bytes} bytes; "
                            f"the configured limit is {settings.MAX_PATCH_BYTES} bytes."
                        ),
                    )
                total_patch_bytes += patch_bytes
                if total_patch_bytes > settings.MAX_DIFF_BYTES:
                    raise InputLimitExceededError(
                        code="diff_too_large",
                        message=(
                            "Combined file patches exceed the configured "
                            f"{settings.MAX_DIFF_BYTES}-byte limit."
                        ),
                    )
                files.append(
                    PRFile(
                        filename=item["filename"],
                        status=item["status"],
                        additions=item.get("additions", 0),
                        deletions=item.get("deletions", 0),
                        patch=patch,
                        previous_filename=item.get("previous_filename"),
                        patch_status="available" if patch is not None else "unavailable",
                    )
                )
            if len(items) < 100 and 'rel="next"' not in response.headers.get("link", ""):
                break
            page += 1
        return files

    async def get_file_content(
        self, repo: str, file_path: str, ref: str, token: str
    ) -> Optional[str]:
        """
        Fetch the full content of a file at a specific commit/branch ref.

        Used by the RAG indexer (Phase 5) to fetch surrounding file context
        beyond what's visible in the diff.

        Args:
            repo:      Repository full name.
            file_path: Path to the file, e.g. "src/auth.py"
            ref:       Branch name or commit SHA.
            token:     GitHub Installation Access Token.

        Returns:
            The decoded file content as a string, or None if not found.
        """
        import base64

        url = f"{self._repo_url(repo)}/contents/{file_path}"
        self._logger.debug("fetching_file_content", repo=repo, path=file_path, ref=ref)

        try:
            async with httpx.AsyncClient(timeout=settings.GITHUB_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    url,
                    headers=self._headers(token),
                    params={"ref": ref},
                )
        except httpx.TimeoutException as exc:
            raise GitHubClientError(
                code="github_timeout",
                message="GitHub did not respond before the request timeout.",
                retryable=True,
            ) from exc

        if response.status_code == 404:
            self._logger.warning("file_not_found", repo=repo, path=file_path, ref=ref)
            return None

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._http_error(exc) from exc
        data = response.json()

        # GitHub returns file content Base64-encoded
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

        return data.get("content")

    # ── Review & Comments ─────────────────────────────────────────────────────

    async def post_review(
        self,
        repo: str,
        pr_number: int,
        token: str,
        head_sha: str,
        comments: list[ReviewComment],
        summary_body: str,
        action: str = "COMMENT",
    ) -> dict:
        """
        Post a full PR review with inline comments and a summary.

        GitHub groups all inline comments under a single "Review" object,
        which also carries an overall verdict:
          - "COMMENT"          → neutral review, no merge block
          - "REQUEST_CHANGES"  → blocks merge until changes are made
          - "APPROVE"          → approves the PR

        Args:
            repo:         Repository full name.
            pr_number:    The PR number.
            token:        GitHub Installation Access Token.
            head_sha:     The latest commit SHA on the PR (from PRMetadata.head_sha).
            comments:     List of ReviewComment objects for inline annotations.
            summary_body: Markdown text shown as the review summary.
            action:       "COMMENT" | "REQUEST_CHANGES" | "APPROVE"

        Returns:
            The raw GitHub API response dict for the created review.
        """
        url = f"{self._repo_url(repo)}/pulls/{pr_number}/reviews"

        payload = {
            "commit_id": head_sha,
            "body": summary_body,
            "event": action,
            "comments": [
                {
                    "path": c.path,
                    "line": c.line,
                    "side": c.side,
                    "body": c.body,
                }
                for c in comments
            ],
        }

        self._logger.info(
            "posting_review",
            repo=repo,
            pr_number=pr_number,
            gh_action=action,
            inline_comments=len(comments),
        )

        response = await self._post(url, token, json=payload)
        data = response.json()

        self._logger.info(
            "review_posted",
            repo=repo,
            pr_number=pr_number,
            review_id=data.get("id"),
        )
        return data

    async def post_comment(self, repo: str, pr_number: int, token: str, body: str) -> dict:
        """
        Post a general (non-inline) comment on the PR conversation thread.

        Use this for posting the AI review summary when there are no
        specific line-level comments to attach.

        Args:
            repo:      Repository full name.
            pr_number: The PR number.
            token:     GitHub Installation Access Token.
            body:      The comment body (supports Markdown).

        Returns:
            The raw GitHub API response dict for the created comment.
        """
        url = f"{self._repo_url(repo)}/issues/{pr_number}/comments"

        self._logger.info("posting_comment", repo=repo, pr_number=pr_number)
        response = await self._post(url, token, json={"body": body})
        return response.json()

    async def create_check_run(
        self,
        repo: str,
        token: str,
        head_sha: str,
        name: str = "AI PR Review",
        status: str = "completed",
        conclusion: str = "neutral",
        summary: str = "",
    ) -> dict:
        """
        Create or update a GitHub Check Run on a commit.

        Check Runs appear in the PR's "Checks" tab and can show a pass/fail
        status next to the commit. Useful for signalling review completion
        without blocking the merge.

        Args:
            repo:       Repository full name.
            token:      GitHub Installation Access Token.
            head_sha:   The commit SHA to attach the check to.
            name:       Display name for the check in GitHub UI.
            status:     "queued" | "in_progress" | "completed"
            conclusion: "success" | "failure" | "neutral" | "cancelled"
                        (only required when status == "completed")
            summary:    Markdown text shown in the check details panel.

        Returns:
            The raw GitHub API response dict for the created check run.
        """
        url = f"{self._repo_url(repo)}/check-runs"

        payload = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
            "conclusion": conclusion,
            "output": {
                "title": name,
                "summary": summary,
            },
        }

        self._logger.info(
            "creating_check_run",
            repo=repo,
            head_sha=head_sha[:7],
            conclusion=conclusion,
        )

        response = await self._post(url, token, json=payload)
        return response.json()
