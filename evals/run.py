"""Command-line entry point: ``uv run python -m evals.run --split dev``."""

import argparse
import asyncio
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from evals.dataset import DEFAULT_DATASET, load_cases
from evals.ratelimit import RateLimitRetryProvider
from evals.report import RunInfo, git_state, summarize, write_report
from evals.runner import CaseRun, RepeatResult, run_dataset
from evals.scoring import DEFAULT_TOLERANCE
from src.agents.bug_detector import BUG_DETECTOR_PROMPT
from src.config import settings
from src.llm.factory import create_llm_provider

DEFAULT_REPORTS = Path(__file__).parent / "reports"


def prompt_version() -> str:
    """Short hash of the reviewer prompt, so reports show when the prompt changed."""
    return hashlib.sha256(BUG_DETECTOR_PROMPT.encode()).hexdigest()[:12]


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    return (
        str(resolved.relative_to(Path.cwd())) if resolved.is_relative_to(Path.cwd()) else str(path)
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the reviewer evaluation suite.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--provider", choices=["fake", "groq"], default=settings.LLM_PROVIDER)
    parser.add_argument("--model", help="Defaults to LLM_MODEL, or 'fake' with --provider fake")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=2_000,
        help="Output cap per review. Providers count it against token-per-minute limits.",
    )
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--case", action="append", help="Run only these case ids")
    return parser.parse_args()


def _print_progress(run: CaseRun) -> None:
    outcome = run.failure_code or f"TP={run.score.tp} FP={run.score.fp} FN={run.score.fn}"
    print(f"  [{run.repeat}] {run.case.id:<40} {outcome}  ({run.duration_ms} ms)")


def _warn_if_mostly_failed(results: list[RepeatResult]) -> None:
    runs = [run for result in results for run in result.case_runs]
    codes = Counter(run.failure_code for run in runs if run.failure_code)
    if sum(codes.values()) > len(runs) / 2:
        print(f"\n  WARNING: {sum(codes.values())}/{len(runs)} reviews failed {dict(codes)}.")
        print("  These scores do not measure the model; fix the failures first.")


async def main() -> None:
    args = _parse_args()
    if args.model is None:
        args.model = "fake" if args.provider == "fake" else settings.LLM_MODEL
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")

    cases = load_cases(args.dataset, None if args.split == "all" else args.split)
    if args.case:
        cases = [case for case in cases if case.id in set(args.case)]
    if not cases:
        raise SystemExit("No cases selected.")

    provider = RateLimitRetryProvider(
        create_llm_provider(settings.model_copy(update={"LLM_PROVIDER": args.provider}))
    )
    print(f"Running {len(cases)} cases x {args.repeats} with {args.provider}/{args.model}")
    try:
        results = await run_dataset(
            cases,
            provider,
            model_name=args.model,
            repeats=args.repeats,
            tolerance=args.tolerance,
            max_context_chars=settings.MAX_REVIEW_CONTEXT_CHARS,
            max_model_attempts=settings.MAX_MODEL_ATTEMPTS,
            max_output_tokens=args.max_output_tokens,
            timeout=120,
            on_case=_print_progress,
        )
    finally:
        await provider.aclose()

    sha, dirty = git_state()
    info = RunInfo(
        timestamp=datetime.now(timezone.utc),
        dataset=_display_path(args.dataset),
        split=args.split,
        provider=args.provider,
        model=args.model,
        prompt_version=prompt_version(),
        repeats=args.repeats,
        tolerance=args.tolerance,
        git_sha=sha,
        git_dirty=dirty,
    )
    json_path, md_path = write_report(info, results, args.out)

    print()
    for name, stats in summarize(results).items():
        mean = "n/a" if stats["mean"] is None else f"{stats['mean']:.2f}"
        print(f"  {name:<14} {mean}")
    if provider.waits:
        print(f"\n  waited {provider.seconds_waited:.0f}s over {provider.waits} rate-limit retries")
    _warn_if_mostly_failed(results)
    print(f"\nReport: {md_path}\nData:   {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
