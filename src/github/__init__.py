"""GitHub integration package."""
from src.github.auth import GitHubAppAuth
from src.github.client import GitHubClient, PRMetadata, PRFile, ReviewComment
from src.github.diff_parser import FileDiff, DiffHunk, DiffLine, parse_diff

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
]
