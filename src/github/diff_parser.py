"""
Unified Diff Parser.

Parses the raw unified diff (returned by GitHub's PR API) into structured
Python objects that the AI agents can reason about.

Each parsed file contains:
  - The filename
  - The detected language
  - Whether it's new, deleted, renamed, or binary
  - A list of hunks, each containing individual lines with their line numbers

Line numbers are critical — GitHub requires exact line numbers to post
inline review comments. If line numbers are wrong, comments fail silently.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

# ── Language detection map ────────────────────────────────────────────────────
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".sh": "Shell",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".md": "Markdown",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
}


def detect_language(file_path: str) -> str:
    """Detect programming language from file extension."""
    for ext, lang in EXTENSION_TO_LANGUAGE.items():
        if file_path.endswith(ext):
            return lang
    return "Unknown"


# ── Data models ───────────────────────────────────────────────────────────────

LineType = Literal["addition", "deletion", "context"]


@dataclass
class DiffLine:
    """A single line within a diff hunk."""
    type: LineType      # "addition", "deletion", or "context"
    content: str        # The line content (prefix +/-/space stripped)
    old_line_no: int | None = None  # Line number in the original file (None for additions)
    new_line_no: int | None = None  # Line number in the new file (None for deletions)


@dataclass
class DiffHunk:
    """A contiguous block of changes within a file."""
    old_start: int      # Starting line in the original file
    old_lines: int      # Number of lines from the original file
    new_start: int      # Starting line in the new file
    new_lines: int      # Number of lines in the new file
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    """All changes for a single file in the PR."""
    file_path: str              # Clean file path e.g. "src/auth.py"
    language: str               # Detected language e.g. "Python"
    is_new_file: bool = False   # True if the file was newly created
    is_deleted: bool = False    # True if the file was deleted
    is_binary: bool = False     # True if the file is binary (skip review)
    hunks: list[DiffHunk] = field(default_factory=list)

    @property
    def added_lines(self) -> list[DiffLine]:
        """All added lines across all hunks."""
        return [
            line for hunk in self.hunks
            for line in hunk.lines
            if line.type == "addition"
        ]

    @property
    def deleted_lines(self) -> list[DiffLine]:
        """All deleted lines across all hunks."""
        return [
            line for hunk in self.hunks
            for line in hunk.lines
            if line.type == "deletion"
        ]


# ── Parser ────────────────────────────────────────────────────────────────────

# Matches:  @@ -10,6 +10,8 @@  or  @@ -10 +10 @@  (count is optional)
_HUNK_HEADER = re.compile(r'^@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@')


class UnifiedDiffParser:
    """
    Parses a unified diff string into a list of FileDiff objects.

    Usage:
        parser = UnifiedDiffParser(raw_diff_text)
        files = parser.parse()
        for file in files:
            print(file.file_path, file.language, len(file.hunks))
    """

    def __init__(self, diff_text: str) -> None:
        self._lines = diff_text.splitlines()
        self.parsed_files: list[FileDiff] = []

    def parse(self) -> list[FileDiff]:
        """
        Parse the full diff and return a list of FileDiff objects.
        Skips binary files automatically.
        """
        current_file: FileDiff | None = None
        current_hunk: DiffHunk | None = None

        # Counters for tracking line numbers within a hunk
        old_line_no: int = 0
        new_line_no: int = 0

        for line in self._lines:

            # ── File header: old file path ────────────────────────────────────
            if line.startswith("--- "):
                path = line[4:].strip()
                is_new = path == "/dev/null"
                # Store temporarily; the +++ line gives us the real new path
                current_file = None
                current_hunk = None
                _pending_is_new = is_new
                continue

            # ── File header: new file path ────────────────────────────────────
            if line.startswith("+++ "):
                path = line[4:].strip()
                is_deleted = path == "/dev/null"

                # Strip the "b/" prefix GitHub adds to all file paths
                if path.startswith("b/"):
                    path = path[2:]

                current_file = FileDiff(
                    file_path=path,
                    language=detect_language(path),
                    is_new_file=locals().get("_pending_is_new", False),
                    is_deleted=is_deleted,
                )
                self.parsed_files.append(current_file)
                current_hunk = None
                continue

            # ── Binary file indicator ─────────────────────────────────────────
            if "Binary files" in line:
                if current_file:
                    current_file.is_binary = True
                continue

            # ── Skip lines before any file header is seen ─────────────────────
            if current_file is None or current_file.is_binary:
                continue

            # ── Hunk header: @@ -x,y +a,b @@ ────────────────────────────────
            hunk_match = _HUNK_HEADER.match(line)
            if hunk_match:
                # .groups() returns the 4 captured strings (NOT .group())
                old_start_str, old_len_str, new_start_str, new_len_str = hunk_match.groups()

                current_hunk = DiffHunk(
                    old_start=int(old_start_str),
                    old_lines=int(old_len_str) if old_len_str is not None else 1,
                    new_start=int(new_start_str),
                    new_lines=int(new_len_str) if new_len_str is not None else 1,
                )
                current_file.hunks.append(current_hunk)

                # Reset line number counters for this hunk
                old_line_no = current_hunk.old_start
                new_line_no = current_hunk.new_start
                continue

            # ── No hunk started yet — skip ────────────────────────────────────
            if current_hunk is None:
                continue

            # ── Diff content lines ────────────────────────────────────────────
            if line.startswith("+"):
                current_hunk.lines.append(DiffLine(
                    type="addition",
                    content=line[1:],
                    old_line_no=None,      # Additions don't exist in old file
                    new_line_no=new_line_no,
                ))
                new_line_no += 1

            elif line.startswith("-"):
                current_hunk.lines.append(DiffLine(
                    type="deletion",
                    content=line[1:],
                    old_line_no=old_line_no,
                    new_line_no=None,      # Deletions don't exist in new file
                ))
                old_line_no += 1

            elif line.startswith(" "):
                current_hunk.lines.append(DiffLine(
                    type="context",
                    content=line[1:],
                    old_line_no=old_line_no,
                    new_line_no=new_line_no,
                ))
                old_line_no += 1
                new_line_no += 1

            elif line == "\\ No newline at end of file":
                continue  # Informational only — not a real diff line

        return self.parsed_files


# ── Convenience function ──────────────────────────────────────────────────────

def parse_diff(diff_text: str) -> list[FileDiff]:
    """
    Parse a unified diff string into FileDiff objects.

    Args:
        diff_text: Raw unified diff string from GitHub API.

    Returns:
        List of FileDiff objects, one per changed file.
        Binary files are included but marked with is_binary=True.
    """
    return UnifiedDiffParser(diff_text).parse()