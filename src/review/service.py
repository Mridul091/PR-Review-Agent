"""Fetch a PR snapshot and run the Python bug detector."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from pydantic import ValidationError

from src.agents.bug_detector import BugDetector
from src.agents.reviewer import format_review_context
from src.github.auth import GitHubAppAuth
from src.github.client import GitHubClient, PRMetadata
from src.github.diff_parser import FileDiff
from src.github.errors import GitHubClientError
from src.llm.base import FindingDraft, LLMProvider, ReviewResponseModel
from src.review.schemas import Evidence, Finding, ReviewFailure, ReviewRequest


@dataclass
class ReviewRun:
    """Snapshot metadata, validated findings, and optional provider diagnostics.

    A missing detector_response means either no supported Python changes were
    found or a typed failure prevented or interrupted the model call.
    """

    review_id: str
    started_at: datetime
    request: ReviewRequest
    metadata: PRMetadata | None
    reviewed_files: list[FileDiff]
    detector_response: ReviewResponseModel | None
    findings: list[Finding]
    failure: ReviewFailure | None = None
    model_calls: int = 0


class ReviewService:
    def __init__(
        self,
        auth: GitHubAppAuth,
        github_client: GitHubClient,
        llm_provider: LLMProvider,
        *,
        model_name: str,
        timeout: int = 30,
        max_output_tokens: int = 8_000,
        max_context_chars: int = 20_000,
        max_model_attempts: int = 2,
    ) -> None:
        if not 0 < max_context_chars <= 1_000_000:
            raise ValueError("max_context_chars must be between 1 and 1,000,000")
        if not 1 <= max_model_attempts <= 3:
            raise ValueError("max_model_attempts must be between 1 and 3")
        self._auth = auth
        self._github_client = github_client
        self._llm_provider = llm_provider
        self._model_name = model_name
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens
        self._max_context_chars = max_context_chars
        self._max_model_attempts = max_model_attempts

    async def review(self, request: ReviewRequest) -> ReviewRun:
        """Run ingestion and detection; report recoverable failures as typed results."""
        request = ReviewRequest.model_validate(request)
        started_at = datetime.now(timezone.utc)
        review_id = str(uuid4())

        try:
            installation_token = await self._auth.get_installation_token(request.installation_id)
        except httpx.TimeoutException:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="github_auth_timeout",
                    stage="github",
                    message="GitHub authentication timed out.",
                    retryable=True,
                ),
            )
        except httpx.HTTPStatusError as exc:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="github_auth_failed",
                    stage="github",
                    message="GitHub authentication failed.",
                    retryable=exc.response.status_code in {429} or exc.response.status_code >= 500,
                ),
            )
        except httpx.RequestError:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="github_auth_failed",
                    stage="github",
                    message="GitHub authentication failed.",
                    retryable=True,
                ),
            )
        except Exception:
            # Authentication errors may contain token or key material; never
            # return the underlying exception text to an API caller.
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="github_auth_failed",
                    stage="github",
                    message="GitHub authentication failed.",
                ),
            )

        try:
            metadata = await self._github_client.get_pull_request(
                request.repository, request.pull_request_number, installation_token
            )
            self._verify_metadata(request, metadata)
            parsed_diff = await self._github_client.get_pr_diff(
                request.repository, request.pull_request_number, installation_token
            )
            # The PR diff endpoint follows the live PR; reject stale snapshots.
            latest_metadata = await self._github_client.get_pull_request(
                request.repository, request.pull_request_number, installation_token
            )
            self._verify_metadata(request, latest_metadata)
        except GitHubClientError as exc:
            return self._failed_run(request, started_at, review_id, exc.to_failure())
        except httpx.TimeoutException:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="github_timeout",
                    stage="github",
                    message="GitHub did not respond before the request timeout.",
                    retryable=True,
                ),
            )
        except httpx.HTTPStatusError as exc:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="github_http_error",
                    stage="github",
                    message="GitHub API request failed.",
                    retryable=exc.response.status_code == 429 or exc.response.status_code >= 500,
                ),
            )
        except httpx.RequestError:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="github_network_error",
                    stage="github",
                    message="GitHub API request failed.",
                    retryable=True,
                ),
            )
        except ValueError:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="invalid_diff",
                    stage="input",
                    message="GitHub returned an invalid pull request diff.",
                ),
            )
        except Exception:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="github_ingestion_failed",
                    stage="github",
                    message="Pull request input could not be fetched.",
                ),
            )

        python_files = [
            file_diff
            for file_diff in parsed_diff
            if not file_diff.is_binary
            and file_diff.hunks
            and any(
                path is not None and path.endswith(".py")
                for path in (file_diff.file_path, file_diff.old_path, file_diff.new_path)
            )
        ]

        # Budget the exact text that Review will send to the model. Do not truncate
        # the diff: truncation could hide files or invalidate line references.
        if python_files and len(format_review_context(python_files)) > self._max_context_chars:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="review_context_too_large",
                    stage="input",
                    message="Python changes exceed the configured review context limit.",
                ),
                metadata=latest_metadata,
                reviewed_files=python_files,
            )

        response = None
        model_calls = 0
        if python_files:
            detector = BugDetector(
                llm_provider=self._llm_provider,
                parsed_diff=python_files,
                model_name=self._model_name,
                timeout=self._timeout,
                max_output_tokens=self._max_output_tokens,
                max_attempts=self._max_model_attempts,
            )
            outcome = await detector.detect_bugs()
            response = outcome.response
            model_calls = outcome.model_calls
            if outcome.failure is not None:
                return self._failed_run(
                    request,
                    started_at,
                    review_id,
                    outcome.failure,
                    metadata=latest_metadata,
                    reviewed_files=python_files,
                    model_calls=model_calls,
                )

        try:
            findings = self._construct_findings(
                request=request,
                review_id=review_id,
                reviewed_files=python_files,
                response=response,
            )
        except ValidationError:
            return self._failed_run(
                request,
                started_at,
                review_id,
                ReviewFailure(
                    code="malformed_model_output",
                    stage="review",
                    message="The model returned an invalid review response.",
                    agent="bug_detector",
                ),
                metadata=latest_metadata,
                reviewed_files=python_files,
                model_calls=model_calls,
            )

        return ReviewRun(
            review_id=review_id,
            started_at=started_at,
            request=request,
            metadata=latest_metadata,
            reviewed_files=python_files,
            detector_response=response,
            findings=findings,
            model_calls=model_calls,
        )

    @staticmethod
    def _failed_run(
        request: ReviewRequest,
        started_at: datetime,
        review_id: str,
        failure: ReviewFailure,
        *,
        metadata: PRMetadata | None = None,
        reviewed_files: list[FileDiff] | None = None,
        model_calls: int = 0,
    ) -> ReviewRun:
        return ReviewRun(
            review_id=review_id,
            started_at=started_at,
            request=request,
            metadata=metadata,
            reviewed_files=reviewed_files if reviewed_files is not None else [],
            detector_response=None,
            findings=[],
            failure=failure,
            model_calls=model_calls,
        )

    @classmethod
    def _construct_findings(
        cls,
        *,
        request: ReviewRequest,
        review_id: str,
        reviewed_files: list[FileDiff],
        response: ReviewResponseModel | None,
    ) -> list[Finding]:
        """Convert validated provider drafts into trusted, publishable findings."""
        if response is None:
            return []

        files_by_path: dict[str, FileDiff] = {}
        for file_diff in reviewed_files:
            for path in (file_diff.file_path, file_diff.old_path, file_diff.new_path):
                if path:
                    files_by_path[path] = file_diff

        findings: list[Finding] = []
        for index, draft in enumerate(response.draft_findings, start=1):
            file_diff = files_by_path.get(draft.file_path)
            if file_diff is None or file_diff.is_binary:
                continue
            if not cls._line_location_is_valid(
                file_diff,
                draft.line_start,
                draft.line_end,
                draft.side,
            ):
                continue
            if draft.side is not None and draft.line_start is None:
                continue
            if draft.line_start is not None and draft.side is None:
                continue

            evidence = cls._construct_diff_evidence(
                request=request,
                draft=draft,
                files_by_path=files_by_path,
            )
            if not evidence:
                continue

            findings.append(
                Finding(
                    id=f"{review_id}:bug_detector:{index}",
                    category=draft.category,
                    severity=draft.severity,
                    confidence=draft.confidence,
                    file_path=file_diff.file_path,
                    line_start=draft.line_start,
                    line_end=draft.line_end,
                    side=draft.side,
                    title=draft.title,
                    defect=draft.defect,
                    trigger=draft.trigger,
                    impact=draft.impact,
                    recommendation=draft.recommendation,
                    agent="bug_detector",
                    evidence=evidence,
                )
            )

        return findings

    @classmethod
    def _construct_diff_evidence(
        cls,
        *,
        request: ReviewRequest,
        draft: FindingDraft,
        files_by_path: dict[str, FileDiff],
    ) -> list[Evidence]:
        """Keep only diff evidence that matches the reviewed snapshot."""
        commit_sha = request.base_sha if draft.side == "LEFT" else request.head_sha
        evidence: list[Evidence] = []
        for item in draft.evidence:
            if item.source != "diff":
                continue
            if item.line_start is not None and draft.side is None:
                continue
            file_diff = files_by_path.get(item.file_path)
            if file_diff is None or file_diff.is_binary:
                continue
            if not cls._line_location_is_valid(
                file_diff,
                item.line_start,
                item.line_end,
                draft.side,
            ):
                continue
            evidence.append(
                Evidence(
                    source="diff",
                    file_path=file_diff.file_path,
                    commit_sha=commit_sha,
                    line_start=item.line_start,
                    line_end=item.line_end,
                    excerpt=item.excerpt,
                )
            )
        return evidence

    @staticmethod
    def _line_location_is_valid(
        file_diff: FileDiff,
        line_start: int | None,
        line_end: int | None,
        side: str | None,
    ) -> bool:
        """Check that a reported range exists on the requested diff side."""
        if line_end is not None and line_start is None:
            return False
        if line_start is None:
            return True
        if line_end is not None and line_end < line_start:
            return False

        line_numbers: set[int] = set()
        for hunk in file_diff.hunks:
            for line in hunk.lines:
                if side in (None, "LEFT") and line.old_line_no is not None:
                    line_numbers.add(line.old_line_no)
                if side in (None, "RIGHT") and line.new_line_no is not None:
                    line_numbers.add(line.new_line_no)

        return line_start in line_numbers and (line_end is None or line_end in line_numbers)

    @staticmethod
    def _verify_metadata(request: ReviewRequest, metadata: PRMetadata) -> None:
        """Require the requested target repository, PR number, and full commit IDs."""
        if metadata.target_repo_full_name.casefold() != request.repository.casefold():
            raise GitHubClientError(
                code="repository_identity_mismatch",
                message="Pull request belongs to a different target repository.",
            )
        if metadata.number != request.pull_request_number:
            raise GitHubClientError(
                code="pull_request_identity_mismatch",
                message="GitHub returned a different pull request.",
            )
        if metadata.base_sha.lower() != request.base_sha.lower():
            raise GitHubClientError(
                code="base_sha_mismatch",
                message="Pull request base commit differs from the requested snapshot.",
            )
        if metadata.head_sha.lower() != request.head_sha.lower():
            raise GitHubClientError(
                code="head_sha_mismatch",
                message="Pull request head commit differs from the requested snapshot.",
            )
