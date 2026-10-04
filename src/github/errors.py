"""Typed, user-safe errors raised while ingesting GitHub review input."""

from dataclasses import dataclass

from src.review.schemas import ReviewFailure


@dataclass
class GitHubClientError(Exception):
    code: str
    message: str
    retryable: bool = False
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message

    def to_failure(self) -> ReviewFailure:
        return ReviewFailure(
            code=self.code,
            stage="github",
            message=self.message,
            retryable=self.retryable,
        )


class InputLimitExceededError(GitHubClientError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code=code, message=message, retryable=False)

    def to_failure(self) -> ReviewFailure:
        return ReviewFailure(
            code=self.code,
            stage="input",
            message=self.message,
            retryable=False,
        )
