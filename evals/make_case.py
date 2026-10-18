"""Create an eval case from before/after versions of a file.

    uv run python -m evals.make_case off_by_one_pagination \\
        --path src/pagination.py --before before.py --after after.py

Writes ``diff.patch`` and, if missing, a ``case.toml`` template, then prints the
new-file line numbers of every added line so you can fill in ``expected``.
Omit ``--before`` for a newly added file.
"""

import argparse
import difflib
from pathlib import Path

from evals.dataset import DEFAULT_DATASET
from src.github.diff_parser import parse_diff

_TEMPLATE = """id = "{case_id}"
description = "TODO: what the PR does and, for bug cases, what is wrong."
split = "{split}"
tags = []

# Delete this block for a clean case and set: expected = []
[[expected]]
file_path = "{path}"
line_start = 0  # TODO: new-file line number of the defect (see make_case output)
side = "RIGHT"
category = "bug"
defect_type = "todo"
summary = "TODO: one sentence describing the defect."
"""


def make_patch(path: str, before: str, after: str) -> str:
    """Build a git-style unified diff for one file."""
    if before and not before.endswith("\n"):
        before += "\n"
    if after and not after.endswith("\n"):
        after += "\n"
    header = [f"diff --git a/{path} b/{path}\n"]
    if not before:
        header.append("new file mode 100644\n")
    body = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="/dev/null" if not before else f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if not body:
        raise ValueError("before and after are identical")
    return "".join(header + body)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("case_id")
    parser.add_argument("--path", required=True, help="File path as it appears in the PR")
    parser.add_argument("--before", type=Path, help="Original file (omit for a new file)")
    parser.add_argument("--after", type=Path, required=True, help="Changed file")
    parser.add_argument("--split", choices=["dev", "heldout"], default="dev")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()

    before = args.before.read_text() if args.before else ""
    patch = make_patch(args.path, before, args.after.read_text())

    case_dir = args.dataset / "cases" / args.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "diff.patch").write_text(patch)
    case_file = case_dir / "case.toml"
    if not case_file.exists():
        case_file.write_text(
            _TEMPLATE.format(case_id=args.case_id, split=args.split, path=args.path)
        )

    print(f"Wrote {case_dir}/diff.patch\n\nAdded lines (new-file line numbers):")
    for file_diff in parse_diff(patch):
        for line in file_diff.added_lines:
            print(f"  {line.new_line_no:>4} | {line.content}")


if __name__ == "__main__":
    main()
