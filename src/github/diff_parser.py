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
import shlex
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

    type: LineType  # "addition", "deletion", or "context"
    content: str  # The line content (prefix +/-/space stripped)
    old_line_no: int | None = None  # Line number in the original file (None for additions)
    new_line_no: int | None = None  # Line number in the new file (None for deletions)


@dataclass
class DiffHunk:
    """A contiguous block of changes within a file."""

    old_start: int  # Starting line in the original file
    old_lines: int  # Number of lines from the original file
    new_start: int  # Starting line in the new file
    new_lines: int  # Number of lines in the new file
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    """All changes for a single file in the PR."""

    file_path: str  # Clean file path e.g. "src/auth.py"
    language: str  # Detected language e.g. "Python"
    is_new_file: bool = False  # True if the file was newly created
    is_deleted: bool = False  # True if the file was deleted
    is_binary: bool = False  # True if the file is binary (skip review)
    hunks: list[DiffHunk] = field(default_factory=list)
    old_path: str | None = None
    new_path: str | None = None

    @property
    def is_renamed(self) -> bool:
        return (
            self.old_path is not None
            and self.new_path is not None
            and self.old_path != self.new_path
        )

    @property
    def added_lines(self) -> list[DiffLine]:
        """All added lines across all hunks."""
        return [line for hunk in self.hunks for line in hunk.lines if line.type == "addition"]

    @property
    def deleted_lines(self) -> list[DiffLine]:
        """All deleted lines across all hunks."""
        return [line for hunk in self.hunks for line in hunk.lines if line.type == "deletion"]


# ── Parser ────────────────────────────────────────────────────────────────────

# Matches:  @@ -10,6 +10,8 @@  or  @@ -10 +10 @@  (count is optional)
_HUNK_HEADER = re.compile(r"^@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@")


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
        """Parse files independently; retain binary metadata without parsing its payload."""
        self.parsed_files = []
        current_file: FileDiff | None = None
        index = 0
        headers_seen = False
        while index < len(self._lines):
            line = self._lines[index]
            if line.startswith("diff --git "):
                current_file = self._start_file(line)
                headers_seen = False
            elif line.startswith("--- "):
                current_file, index = self._read_file_headers(
                    index, None if headers_seen else current_file
                )
                headers_seen = True
            elif current_file is not None:
                index = self._read_file_line(index, current_file)
            index += 1
        return self.parsed_files

    def _start_file(self, line: str) -> FileDiff:
        paths = line[len("diff --git ") :]
        # Git leaves ordinary spaces unquoted; quoted paths need tokenization.
        if paths.startswith('"'):
            old_path, new_path = shlex.split(paths)
        else:
            old_path, new_path = paths.rsplit(" b/", 1)
            new_path = "b/" + new_path
        result = FileDiff(file_path="", language="Unknown")
        self._set_paths(result, old_path, new_path)
        self.parsed_files.append(result)
        return result

    @staticmethod
    def _set_paths(result: FileDiff, old_path: str, new_path: str) -> None:
        result.old_path = None if old_path == "/dev/null" else old_path.removeprefix("a/")
        result.new_path = None if new_path == "/dev/null" else new_path.removeprefix("b/")
        result.file_path = result.new_path or result.old_path or ""
        result.language = detect_language(result.file_path)
        result.is_new_file = result.old_path is None
        result.is_deleted = result.new_path is None

    def _read_file_headers(self, index: int, current_file: FileDiff | None) -> tuple[FileDiff, int]:
        if index + 1 >= len(self._lines) or not self._lines[index + 1].startswith("+++ "):
            raise ValueError("Missing new-file header in unified diff")
        # A plain unified diff has no diff --git boundary between files.
        if current_file is None or current_file.hunks:
            current_file = FileDiff(file_path="", language="Unknown")
            self.parsed_files.append(current_file)
        old_path = self._lines[index][4:].split("\t", 1)[0]
        new_path = self._lines[index + 1][4:].split("\t", 1)[0]
        old_path = shlex.split(old_path)[0] if old_path.startswith('"') else old_path
        new_path = shlex.split(new_path)[0] if new_path.startswith('"') else new_path
        self._set_paths(current_file, old_path, new_path)
        return current_file, index + 1

    def _read_file_line(self, index: int, result: FileDiff) -> int:
        line = self._lines[index]
        if result.is_binary:
            return index
        if line.startswith("Binary files ") or line == "GIT binary patch":
            result.is_binary = True
        elif line.startswith("new file mode "):
            result.is_new_file = True
            result.old_path = None
        elif line.startswith("deleted file mode "):
            result.is_deleted = True
            result.new_path = None
        elif _HUNK_HEADER.match(line):
            hunk, index = self._read_hunk(index)
            result.hunks.append(hunk)
        return index

    def _read_hunk(self, index: int) -> tuple[DiffHunk, int]:
        match = _HUNK_HEADER.match(self._lines[index])
        if match is None:
            raise ValueError("Invalid hunk header")
        old_start, old_count, new_start, new_count = match.groups()
        hunk = DiffHunk(
            int(old_start),
            int(old_count) if old_count is not None else 1,
            int(new_start),
            int(new_count) if new_count is not None else 1,
        )
        old_line, new_line = hunk.old_start, hunk.new_start
        old_end, new_end = old_line + hunk.old_lines, new_line + hunk.new_lines
        while old_line < old_end or new_line < new_end:
            index += 1
            if index >= len(self._lines):
                raise ValueError("Truncated unified diff hunk")
            line = self._lines[index]
            if line == "\\ No newline at end of file":
                continue
            parsed = self._parse_content_line(line, old_line, new_line)
            old_line += int(parsed.old_line_no is not None)
            new_line += int(parsed.new_line_no is not None)
            if old_line > old_end or new_line > new_end:
                raise ValueError("Hunk content exceeds declared line counts")
            hunk.lines.append(parsed)
        return hunk, index

    @staticmethod
    def _parse_content_line(line: str, old_line: int, new_line: int) -> DiffLine:
        if line.startswith("+"):
            return DiffLine("addition", line[1:], new_line_no=new_line)
        if line.startswith("-"):
            return DiffLine("deletion", line[1:], old_line_no=old_line)
        if line.startswith(" "):
            return DiffLine("context", line[1:], old_line, new_line)
        raise ValueError("Invalid content line in unified diff hunk")


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
