"""Regression tests for unified diff parsing."""

import unittest

import pytest

from src.github.diff_parser import UnifiedDiffParser, parse_diff


@pytest.mark.parametrize(
    ("fixture_name", "expected_path", "attribute"),
    [
        ("added", "new.py", "is_new_file"),
        ("modified", "app.py", None),
        ("deleted", "old.py", "is_deleted"),
        ("renamed", "new.py", "is_renamed"),
        ("binary", "logo.png", "is_binary"),
    ],
)
def test_file_kind_fixtures(diff_fixture, fixture_name, expected_path, attribute):
    parsed = parse_diff(diff_fixture(fixture_name))
    assert len(parsed) == 1
    assert parsed[0].file_path == expected_path
    if attribute:
        assert getattr(parsed[0], attribute)


def test_empty_fixture(diff_fixture):
    assert parse_diff(diff_fixture("empty")) == []


def test_multi_hunk_fixture(diff_fixture):
    parsed = parse_diff(diff_fixture("multi_hunk"))
    assert len(parsed[0].hunks) == 2
    assert [line.new_line_no for line in parsed[0].added_lines] == [2, 10]


class DiffParserTests(unittest.TestCase):
    def test_header_like_content_stays_in_hunk(self):
        files = parse_diff(
            "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n--- old heading\n+++ new heading\n"
        )
        assert len(files) == 1
        assert files[0].file_path == "a.txt"
        assert files[0].deleted_lines[0].content == "-- old heading"
        assert files[0].added_lines[0].content == "++ new heading"
        assert files[0].added_lines[0].new_line_no == 1

    def test_binary_file_does_not_change_preceding_text_file(self):
        files = parse_diff(
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/p.png b/p.png\n"
            "Binary files a/p.png and b/p.png differ\n"
        )
        assert [(f.file_path, f.is_binary) for f in files] == [
            ("a.py", False),
            ("p.png", True),
        ]
        assert files[0].added_lines[0].content == "new"

    def test_deleted_file_keeps_original_path(self):
        file = parse_diff("--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n")[0]
        assert (file.file_path, file.old_path, file.new_path) == (
            "old.py",
            "old.py",
            None,
        )
        assert file.is_deleted and file.language == "Python"
        assert file.deleted_lines[0].old_line_no == 1

    def test_new_file_and_repeated_parse(self):
        parser = UnifiedDiffParser("--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+new\n")
        first = parser.parse()
        assert first[0].is_new_file
        assert first[0].old_path is None
        assert parser.parse() == first
        assert len(parser.parsed_files) == 1

    def test_multiple_hunks_and_no_newline_marker(self):
        file = parse_diff(
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -2,2 +2,2 @@\n"
            " same\n"
            "-old\n"
            "\\ No newline at end of file\n"
            "+new\n"
            "\\ No newline at end of file\n"
            "@@ -10 +10 @@\n"
            "-last\n"
            "+next\n"
        )[0]
        assert len(file.hunks) == 2
        assert [(line.old_line_no, line.new_line_no) for line in file.hunks[0].lines] == [
            (2, 2),
            (3, None),
            (None, 3),
        ]
        assert file.added_lines[-1].new_line_no == 10

    def test_plain_unified_diff_has_independent_files(self):
        files = parse_diff(
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "--- a/c.py\n"
            "+++ b/c.py\n"
            "@@ -1 +1 @@\n"
            "-c\n"
            "+d\n"
        )
        assert [f.file_path for f in files] == ["a.py", "c.py"]

    def test_rename_without_hunks(self):
        file = parse_diff(
            "diff --git a/old.py b/new.py\n"
            "similarity index 100%\n"
            "rename from old.py\n"
            "rename to new.py\n"
        )[0]
        assert file.is_renamed
        assert (file.old_path, file.new_path) == ("old.py", "new.py")
        assert file.hunks == []

    def test_binary_modes(self):
        for mode, flag in [
            ("new file mode 100644", "is_new_file"),
            ("deleted file mode 100644", "is_deleted"),
        ]:
            file = parse_diff(f"diff --git a/p.png b/p.png\n{mode}\nGIT binary patch\nliteral 0\n")[
                0
            ]
            assert file.is_binary and getattr(file, flag)
            assert file.file_path == "p.png"

    def test_binary_marker_in_content_is_plain_text(self):
        file = parse_diff("--- a/a.txt\n+++ b/a.txt\n@@ -0,0 +1 @@\n+Binary files are data\n")[0]
        assert not file.is_binary
        assert file.added_lines[0].content == "Binary files are data"

    def test_empty_diff(self):
        for diff in ["", "unrelated preamble\n"]:
            assert parse_diff(diff) == []

    def test_malformed_or_truncated_diff_is_explicit(self):
        for diff in [
            "--- a/a.py\n",
            "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n",
            "--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n-invalid\n",
        ]:
            with self.assertRaises(ValueError):
                parse_diff(diff)

    def test_empty_plain_files_remain_separate(self):
        files = parse_diff("--- a/a.py\n+++ b/a.py\n--- a/b.py\n+++ b/b.py\n")
        self.assertEqual([f.file_path for f in files], ["a.py", "b.py"])

    def test_quoted_paths_with_spaces(self):
        files = parse_diff(
            'diff --git "a/my file.py" "b/my file.py"\n'
            '--- "a/my file.py"\n'
            '+++ "b/my file.py"\n'
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        self.assertEqual(files[0].file_path, "my file.py")


if __name__ == "__main__":
    unittest.main()
