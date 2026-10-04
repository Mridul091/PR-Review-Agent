"""Validation tests for public review contracts."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.review.schemas import Evidence, Finding, ReviewFailure, ReviewRequest, ReviewResult


def valid_request(**overrides):
    values = {
        "repository": "owner/repo",
        "installation_id": 12,
        "pull_request_number": 4,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
    }
    values.update(overrides)
    return ReviewRequest(**values)


def test_review_request_has_commit_and_repository_identity_without_credentials():
    request = valid_request()
    assert request.repository == "owner/repo"
    assert request.base_sha != request.head_sha
    assert "token" not in request.model_dump()


def test_review_request_rejects_credentials_and_invalid_identity():
    with pytest.raises(ValidationError):
        valid_request(token="secret")
    with pytest.raises(ValidationError):
        valid_request(repository="missing-owner")
    with pytest.raises(ValidationError):
        valid_request(base_sha="not-a-sha")


def test_evidence_and_finding_validate_locations():
    evidence = Evidence(
        source="diff",
        file_path="src/app.py",
        commit_sha="b" * 40,
        line_start=10,
        line_end=12,
    )
    finding = Finding(
        id="bug-1",
        category="bug",
        severity="high",
        confidence=0.9,
        file_path="src/app.py",
        line_start=10,
        side="RIGHT",
        title="Unchecked value",
        defect="The value can be absent.",
        trigger="The value is missing at runtime.",
        impact="The review operation can fail.",
        recommendation="Handle the missing value.",
        agent="bug-detector",
        evidence=[evidence],
    )
    assert finding.evidence == [evidence]

    with pytest.raises(ValidationError, match="line_end requires line_start"):
        Evidence(
            source="diff",
            file_path="src/app.py",
            commit_sha="b" * 40,
            line_end=12,
        )


def test_failed_result_requires_typed_failure():
    now = datetime.now(timezone.utc)
    common = {
        "review_id": "review-1",
        "request": valid_request(),
        "status": "failed",
        "started_at": now,
        "finished_at": now + timedelta(seconds=1),
        "duration_ms": 1_000,
    }
    with pytest.raises(ValidationError, match="require at least one failure"):
        ReviewResult(**common)

    result = ReviewResult(
        **common,
        failures=[
            ReviewFailure(
                code="diff_too_large",
                stage="input",
                message="Diff exceeds the configured limit.",
            )
        ],
    )
    assert result.failures[0].code == "diff_too_large"
