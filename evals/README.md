# Evaluation harness

Measures how well the reviewer finds real bugs without flagging correct code. Each case is a small pull request with known answers; the harness runs it through the real `ReviewService` (only GitHub is replaced by a local stand-in), compares the findings with the labels, and writes a report.

```text
datasets/v1/cases/<id>/diff.patch  ->  ReviewService + LLM  ->  findings
datasets/v1/cases/<id>/case.toml   ->  expected findings    ->  scoring  ->  reports/
```

## Running

```bash
make eval-fake                         # free smoke run with the fake provider (checks the harness only)
make eval                              # dev split with LLM_PROVIDER / LLM_MODEL from .env
make eval ARGS="--repeats 3"           # repeat each case to see run-to-run variation
make eval ARGS="--split heldout"       # held-out check; see "Dev vs held-out" below
make eval ARGS="--case missing_await_on_save"
```

`make eval` calls the real model and costs tokens. Reports go to `evals/reports/` as a Markdown file to read and a JSON file for tooling. Commit the reports you want to keep as reference points, such as the first baseline.

## Metrics

| Metric | Meaning | Why it matters |
|---|---|---|
| **precision** | Of all findings reported, the fraction that matched a real bug | Low precision makes the bot noisy, so people ignore it |
| **recall** | Of all labelled bugs, the fraction the reviewer found | Low recall means bugs get through |
| **f1** | Harmonic mean of precision and recall | One number for comparing runs |
| **clean_fp_rate** | Fraction of clean cases with at least one finding | Shows whether the bot complains about correct code |

The report also records exact-location and category-agreement rates for matched findings, failed reviews, latency, and token counts. `n/a` means the metric has no data, for example precision when nothing was reported.

## Matching rules

A finding counts as a true positive when it has:

1. the same `file_path` as the expected finding,
2. the same diff side (`RIGHT` = new code, `LEFT` = old code), and
3. a line range within ±3 lines of the expected range (`--tolerance`).

A finding without line numbers matches on file alone but is not counted as an exact location. Matching is one-to-one, closest first, so two findings for the same bug count as one true positive and one false positive. A different category still counts as a match, but the disagreement is recorded in the category-match rate. A failed review (provider error, malformed output) counts every expected finding as missed.

Location matching cannot tell whether the finding describes the *right* defect at that line. Error analysis catches this for now; an LLM judge can automate it later (see the next steps below).

## Writing a case

1. Write the original file and the changed file somewhere outside the repo.
2. Generate the diff and a template:

   ```bash
   uv run python -m evals.make_case <case_id> --path src/module.py \
       --before before.py --after after.py
   ```

   It prints the new-file line number of every added line. Omit `--before` for a new file.
3. Edit `case.toml`: write the description, set `line_start` to the defect's line, and choose a `defect_type` (for example `off_by_one`, `missing_await`, `none_dereference`, `wrong_exception`, `resource_leak`, `mutable_default`). For a clean case, delete the `[[expected]]` block and set `expected = []`.
4. Run `make test`. `tests/test_eval_dataset.py` checks that every labelled line exists in the diff.

`case.toml` fields:

```toml
id = "missing_await_on_save"     # must match the directory name
description = "What the PR does and what is wrong."
split = "dev"                    # dev | heldout
tags = ["async"]

[[expected]]                     # repeat for multiple bugs; use `expected = []` for clean cases
file_path = "app/jobs.py"
line_start = 13
line_end = 13                    # optional
side = "RIGHT"                   # RIGHT (new code) or LEFT (deleted code)
category = "bug"                 # security | bug | quality | performance | testing
defect_type = "missing_await"
summary = "One sentence describing the defect."
```

### What makes a good dataset

- **About one clean case for every two bug cases.** Without clean cases you can't measure false positives.
- **One bug per case at first.** Multi-bug cases are harder to analyse.
- **Realistic size.** A 10–40 line diff with surrounding context, not a single changed line.
- **Variety.** Logic errors, async mistakes, error handling, resource handling, wrong API use. Include bugs that only make sense with context from another file; those later measure whether RAG helps.
- **Tricky clean cases.** Code that looks suspicious but is correct, such as an intentional broad `except` that logs and re-raises.
- **Real bugs.** [BugsInPy](https://github.com/soarsmu/BugsInPy) has real Python bugs with their fixes. The fix commit's parent is the "after" side of a buggy PR.

## Dev vs held-out

Look at the `dev` cases while improving prompts. Mark roughly a third of cases as `heldout`, and run them only to confirm that an improvement generalises. If you tune the prompt until the held-out cases pass, they stop measuring anything. Keep variants of the same bug in the same split.

## Workflow

1. Run `make eval ARGS="--repeats 3"` to get a baseline, and commit the report.
2. Read the **Error analysis** section of the report and fill in a note for every miss and false positive.
3. Group the notes into categories ("flags style as bugs", "wrong line numbers", "misses async issues").
4. Change one thing (prompt, model, context) to address the largest category.
5. Re-run and compare. Check the held-out split before deciding the change is an improvement.

## Next steps

- [ ] Grow the dataset to about 20 cases (about 7 clean), with roughly a third held out.
- [x] Record the first real baseline and commit the report (`reports/20261018-075131-dev-qwen-qwen3.8-27b.md`).
- [ ] Record real model responses and replay them in CI, so scoring changes are tested against realistic output without paying for model calls.
- [ ] Add an LLM judge that checks whether a matched finding describes the labelled defect; calibrate it against your own judgements on about 30 findings.
- [ ] Track cost per review once `estimated_cost_usd` is computed (roadmap section B).
