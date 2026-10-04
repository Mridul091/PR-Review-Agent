"""GitHub integration package."""

from src.github.auth import GitHubAppAuth
from src.github.client import GitHubClient, PRFile, PRMetadata, ReviewComment
from src.github.diff_parser import DiffHunk, DiffLine, FileDiff, parse_diff
from src.github.errors import GitHubClientError, InputLimitExceededError

__all__ = [
    "GitHubAppAuth",
    "GitHubClient",
    "PRMetadata",
    "PRFile",
    "ReviewComment",
    "FileDiff",
    "DiffHunk",
    "DiffLine",
    "parse_diff",
    "GitHubClientError",
    "InputLimitExceededError",
]
