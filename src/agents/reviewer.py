"""Review-agent orchestration helpers."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from src.github.diff_parser import DiffLine, FileDiff
from src.llm.base import (
    EvidenceDraft,
    FindingDraft,
    LLMProvider,
    MalformedProviderOutputError,
    OutputConstraints,
    ReviewModelRequest,
    ReviewResponseModel,
    TransientProviderError,
)
from src.review.schemas import ReviewFailure


@dataclass(frozen=True)
class ReviewOutcome:
    response: ReviewResponseModel | None
    failure: ReviewFailure | None
    model_calls: int


def format_review_context(
    parsed_diff: Sequence[FileDiff] | str,
    repository_context: str | None = None,
) -> str:
    if isinstance(parsed_diff, str):
        diff_text = parsed_diff.strip()
    else:
        diff_text = _format_parsed_diff(parsed_diff)

    repository_text = (repository_context or "").strip()
    if not repository_text:
        repository_text = "No repository context was provided."

    return (
        "=== DIFF ===\n"
        f"{diff_text or 'No changed files were provided.'}\n\n"
        "=== REPOSITORY CONTEXT ===\n"
        f"{repository_text}"
    )


def _format_parsed_diff(files: Sequence[FileDiff]) -> str:
    sections: list[str] = []

    for file_diff in files:
        status: list[str] = []
        if file_diff.is_new_file:
            status.append("new")
        if file_diff.is_deleted:
            status.append("deleted")
        if file_diff.is_renamed:
            status.append(f"renamed from {file_diff.old_path}")
        if file_diff.is_binary:
            status.append("binary")

        metadata = f"File: {file_diff.file_path}"
        if file_diff.language != "Unknown":
            metadata += f" ({file_diff.language})"
        if status:
            metadata += f" [{', '.join(status)}]"

        file_lines = [metadata]
        if file_diff.is_binary:
            file_lines.append("Binary file; contents are unavailable for review.")

        for hunk in file_diff.hunks:
            file_lines.append(
                f"@@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@"
            )
            for line in hunk.lines:
                file_lines.append(_format_diff_line(line))

        sections.append("\n".join(file_lines))

    return "\n\n".join(sections)


def _format_diff_line(line: DiffLine) -> str:
    prefix = {"addition": "+", "deletion": "-", "context": " "}[line.type]
    old_location = "-" if line.old_line_no is None else str(line.old_line_no)
    new_location = "-" if line.new_line_no is None else str(line.new_line_no)
    return f"{prefix} [{old_location}->{new_location}] {line.content}"


def _valid_line_location(
    file_diff: FileDiff,
    line_start: int | None,
    line_end: int | None,
    side: str | None = None,
) -> bool:
    """Check that a reported line range exists on the requested diff side."""
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


def _valid_evidence(
    evidence: EvidenceDraft,
    files_by_path: dict[str, FileDiff],
) -> bool:
    """Validate evidence locations that can be checked against the diff."""
    if evidence.line_end is not None and evidence.line_start is None:
        return False
    if (
        evidence.line_start is not None
        and evidence.line_end is not None
        and evidence.line_end < evidence.line_start
    ):
        return False

    if evidence.source == "diff":
        file_diff = files_by_path.get(evidence.file_path)
        return file_diff is not None and _valid_line_location(
            file_diff, evidence.line_start, evidence.line_end
        )

    return True


def _valid_draft(
    draft: FindingDraft,
    files_by_path: dict[str, FileDiff],
) -> bool:
    """Validate a provider draft against changed-file metadata and evidence."""
    file_diff = files_by_path.get(draft.file_path)
    if file_diff is None or file_diff.is_binary:
        return False
    if not _valid_line_location(file_diff, draft.line_start, draft.line_end, draft.side):
        return False
    return all(_valid_evidence(item, files_by_path) for item in draft.evidence)


def _filter_valid_drafts(
    response: ReviewResponseModel,
    parsed_diff: Sequence[FileDiff] | str,
) -> ReviewResponseModel:
    """Remove drafts whose locations or diff evidence cannot be verified."""
    if isinstance(parsed_diff, str):
        return response

    files_by_path = {file_diff.file_path: file_diff for file_diff in parsed_diff}
    valid_drafts = [
        draft for draft in response.draft_findings if _valid_draft(draft, files_by_path)
    ]
    return response.model_copy(update={"draft_findings": valid_drafts})


class Review:
    """Coordinate review stages around an injected LLM provider."""

    def __init__(
        self,
        provider: LLMProvider,
        prompt: str,
        model_name: str,
        timeout: int = 30,
        max_output_tokens: int = 8_000,
        allow_empty_findings: bool = True,
        max_attempts: int = 2,
    ) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self._llm_provider = provider
        self._prompt = prompt
        self._model_name = model_name
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens
        self._allow_empty_findings = allow_empty_findings
        self._max_attempts = max_attempts

    @staticmethod
    def build_context(
        parsed_diff: Sequence[FileDiff] | str,
        repository_context: str | None = None,
    ) -> str:
        """Build the provider-facing context for a review request."""
        return format_review_context(parsed_diff, repository_context)

    async def review(
        self,
        parsed_diff: Sequence[FileDiff] | str,
        repository_context: str | None = None,
    ) -> ReviewOutcome:
        formatted_context = self.build_context(parsed_diff, repository_context)
        output_constraints = OutputConstraints(
            schema_version="1",
            max_output_tokens=self._max_output_tokens,
            allow_empty_findings=self._allow_empty_findings,
        )
        request = ReviewModelRequest(
            prompt=self._prompt,
            diff_context=formatted_context,
            output_constraints=output_constraints,
            model_name=self._model_name,
            timeout=self._timeout,
        )

        # One wall-clock budget covers all attempts and backoffs, not one
        # timeout per attempt. Never retry malformed or permanent failures.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + request.timeout
        for attempt in range(1, self._max_attempts + 1):
            try:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return self._failed("provider_timeout", attempt - 1, retryable=True)
                raw = await asyncio.wait_for(
                    self._llm_provider.generate_review(request), timeout=remaining
                )
            except (MalformedProviderOutputError, ValidationError):
                return self._failed("malformed_model_output", attempt, retryable=False)
            except asyncio.TimeoutError:
                return self._failed("provider_timeout", attempt, retryable=True)
            except TransientProviderError:
                if attempt == self._max_attempts:
                    return self._failed("provider_error", attempt, retryable=True)
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return self._failed("provider_timeout", attempt, retryable=True)
                await asyncio.sleep(min(0.1 * 2 ** (attempt - 1), remaining))
                continue
            except Exception:
                # Do not include exception text: SDK errors can contain credentials
                # or raw provider payloads. Cancellation is not an Exception here.
                return self._failed("provider_error", attempt, retryable=False)

            try:
                response = ReviewResponseModel.model_validate(raw)
                return ReviewOutcome(_filter_valid_drafts(response, parsed_diff), None, attempt)
            except (ValidationError, TypeError, AttributeError):
                return self._failed("malformed_model_output", attempt, retryable=False)
        raise AssertionError("unreachable")

    @staticmethod
    def _failed(code: str, model_calls: int, *, retryable: bool) -> ReviewOutcome:
        messages = {
            "provider_timeout": "The model review exceeded its time limit.",
            "malformed_model_output": "The model returned an invalid review response.",
            "provider_error": "The model review could not be completed.",
        }
        return ReviewOutcome(
            response=None,
            failure=ReviewFailure(
                code=code,
                stage="review",
                message=messages[code],
                retryable=retryable,
                agent="bug_detector",
            ),
            model_calls=model_calls,
        )
