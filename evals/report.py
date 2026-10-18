"""Write evaluation results as JSON (for tooling) and Markdown (for reading)."""

import json
import statistics
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from evals.runner import CaseRun, RepeatResult

_HEADLINE_METRICS = ("precision", "recall", "f1", "clean_fp_rate")


@dataclass(frozen=True)
class RunInfo:
    timestamp: datetime
    dataset: str
    split: str
    provider: str
    model: str
    prompt_version: str
    repeats: int
    tolerance: int
    git_sha: str
    git_dirty: bool


def git_state() -> tuple[str, bool]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown", False
    return sha, dirty


def summarize(results: list[RepeatResult]) -> dict[str, dict[str, float | None]]:
    """Mean, min, and max of each headline metric across repeats."""
    summary: dict[str, dict[str, float | None]] = {}
    for name in _HEADLINE_METRICS:
        values = [getattr(r.metrics, name) for r in results]
        present = [v for v in values if v is not None]
        summary[name] = {
            "mean": statistics.fmean(present) if present else None,
            "min": min(present) if present else None,
            "max": max(present) if present else None,
        }
    return summary


def _case_run_json(run: CaseRun) -> dict:
    return {
        "case_id": run.case.id,
        "repeat": run.repeat,
        "status": run.status,
        "failure_code": run.failure_code,
        "duration_ms": run.duration_ms,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "model_calls": run.model_calls,
        "tp": run.score.tp,
        "fp": run.score.fp,
        "fn": run.score.fn,
        "matches": [asdict(match) for match in run.score.matches],
        "findings": [finding.model_dump(mode="json") for finding in run.findings],
    }


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _one_line(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_markdown(info: RunInfo, results: list[RepeatResult]) -> str:
    summary = summarize(results)
    first = results[0].metrics
    lines = [
        f"# Eval report: {info.split} split, {info.model}",
        "",
        f"- **Run:** {info.timestamp:%Y-%m-%d %H:%M UTC}",
        f"- **Dataset:** `{info.dataset}` ({first.cases} cases: "
        f"{first.bug_cases} bug, {first.clean_cases} clean)",
        f"- **Provider / model:** {info.provider} / `{info.model}`",
        f"- **Prompt version:** `{info.prompt_version}`",
        f"- **Code:** `{info.git_sha}`{' (uncommitted changes)' if info.git_dirty else ''}",
        f"- **Repeats:** {info.repeats}, **line tolerance:** ±{info.tolerance}",
        "",
        "## Metrics",
        "",
        "| Metric | Mean | Min | Max |",
        "|---|---|---|---|",
    ]
    for name, stats in summary.items():
        lines.append(
            f"| {name} | {_fmt(stats['mean'])} | {_fmt(stats['min'])} | {_fmt(stats['max'])} |"
        )

    total_tp = sum(r.metrics.tp for r in results)
    total_fp = sum(r.metrics.fp for r in results)
    total_fn = sum(r.metrics.fn for r in results)
    failed = sum(r.metrics.failed_cases for r in results)
    lines += [
        "",
        f"Totals across repeats: {total_tp} TP, {total_fp} FP, {total_fn} FN, "
        f"{failed} failed reviews.",
        "",
        "## Cases",
        "",
        "| Case | Split | Type | TP | FP | FN | Failures | Avg ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for index, case_run in enumerate(results[0].case_runs):
        runs = [r.case_runs[index] for r in results]
        failures = sorted({run.failure_code for run in runs if run.failure_code})
        lines.append(
            f"| `{case_run.case.id}` | {case_run.case.split} | "
            f"{'clean' if case_run.case.is_clean else 'bug'} | "
            f"{sum(r.score.tp for r in runs)} | {sum(r.score.fp for r in runs)} | "
            f"{sum(r.score.fn for r in runs)} | {', '.join(failures) or '—'} | "
            f"{statistics.fmean(r.duration_ms for r in runs):.0f} |"
        )

    lines += [
        "",
        "## Error analysis",
        "",
        "Read each item and write down why it happened. Group the notes into failure "
        "categories; those categories decide what to change next.",
        "",
    ]
    for result in results:
        for case_run in result.case_runs:
            score = case_run.score
            if not (score.fp or score.fn):
                continue
            lines.append(f"### `{case_run.case.id}` (repeat {result.repeat})")
            lines.append("")
            if case_run.failure_code:
                lines.append(f"- **Review failed:** `{case_run.failure_code}`")
            for i in score.missed_indices:
                expected = case_run.case.expected[i]
                location = f"{expected.file_path}:{expected.line_start}"
                lines.append(
                    f"- **Missed** `{expected.defect_type}` at `{location}`: {expected.summary}"
                )
            for i in score.false_positive_indices:
                finding = case_run.findings[i]
                location = f"{finding.file_path}:{finding.line_start or '?'}"
                lines.append(
                    f"- **False positive** at `{location}` ({finding.severity.value}, "
                    f"confidence {finding.confidence:.2f}): **{_one_line(finding.title)}** — "
                    f"{_one_line(finding.defect)}"
                )
            lines.append("- **Notes:** ")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_report(info: RunInfo, results: list[RepeatResult], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = "".join(c if c.isalnum() or c in "-." else "-" for c in info.model)
    stem = f"{info.timestamp:%Y%m%d-%H%M%S}-{info.split}-{safe_model}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    payload = {
        "info": {**asdict(info), "timestamp": info.timestamp.isoformat()},
        "summary": summarize(results),
        "repeats": [
            {
                "repeat": r.repeat,
                "metrics": asdict(r.metrics),
                "cases": [_case_run_json(run) for run in r.case_runs],
            }
            for r in results
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    md_path.write_text(render_markdown(info, results))
    return json_path, md_path
