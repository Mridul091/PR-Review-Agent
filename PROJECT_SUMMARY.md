# Project Summary

Status snapshot of the PR Reviewer Agent: scope, progress, gaps, and next steps. Last updated October 31, 2026.

## Current project scope

This is a Python/FastAPI learning project for a GitHub PR reviewer agent. The intended end goal is a repository-aware AI reviewer with:

- GitHub App webhook ingestion
- PR diff parsing and normalization
- LLM-based bug review
- Structured findings
- Evaluation benchmarks
- LangGraph orchestration
- RAG over repository context
- Security and adversarial tests
- Optional publishing back to GitHub

`ROADMAP.md` frames this as a portfolio project demonstrating AI-agent architecture, RAG, evaluation, and security. It supersedes the build order in `implementation_plan_revised.md`: baseline evaluation comes before RAG, and extra specialist agents and the dashboard are deferred.

## Status at a glance

| Roadmap milestone | Status |
|---|---|
| 1. Reliable input and review contracts | Complete |
| 2. One reviewer working end to end | Complete |
| 3. Baseline evaluation | Nearly done: 30-case dataset (20 dev, 10 held-out), first dev baseline recorded with error analysis; held-out run still to do |
| 4. LangGraph state, verification, recovery | Not started (SQLite result storage already exists) |
| 5. Repository RAG | Not started |
| 6. Agent security and adversarial evaluation | Not started (basic input limits and auth boundaries exist) |
| 7. Approval, publishing, packaging | Not started |

All 88 tests pass and `ruff` reports no issues.

## What is implemented

### FastAPI service

Files: `src/main.py`, `src/api/webhooks.py`, `src/api/review.py`, `src/middleware/auth.py`

Routes:

- `GET /health`
- `POST /api/v1/webhook`
- `POST /api/v1/reviews`
- `GET /api/v1/reviews/{review_id}`

The review API uses bearer-token auth plus an installation/repository allowlist (`REVIEW_REPOSITORY_ACCESS`). Webhooks verify HMAC signatures, enforce a payload-size limit, validate the payload, and run the review in a background task.

### GitHub integration

Files: `src/github/auth.py`, `src/github/client.py`, `src/github/diff_parser.py`

- GitHub App JWT generation and installation token cache
- PR metadata with target/fork repository distinction
- Paginated PR files and bounded raw diff
- Diff parsing into files, hunks, and old/new line numbers
- Limits for file count, diff size, and patch size
- Methods for posting reviews, comments, and check runs (not wired yet)

### Review pipeline

Files: `src/review/service.py`, `src/review/schemas.py`, `src/review/store.py`, `src/agents/reviewer.py`, `src/agents/bug_detector.py`, `src/llm/base.py`, `src/llm/factory.py`, `src/llm/fake.py`, `src/llm/groq_provider.py`

```text
ReviewRequest (from POST /reviews or webhook)
  -> GitHub auth
  -> fetch PR metadata
  -> verify repo/PR/base/head SHA
  -> fetch diff
  -> filter Python files
  -> budget context size
  -> call BugDetector
  -> validate model findings against diff paths/lines
  -> ReviewResult
  -> SQLiteReviewStore
```

- Provider factory selecting `fake` or `groq` from `LLM_PROVIDER` (`fake` is the default and is rejected in production; `GROQ_API_KEY` is required only for `groq`)
- Python-focused bug detector with structured findings (defect, trigger, impact, recommendation, evidence, location)
- Bounded retries for transient errors, one overall model deadline, output-token cap
- Typed failures for GitHub, input, and model errors; model failures are split into `provider_rate_limited`, `provider_unavailable`, `provider_rejected` (4xx) and `provider_error` (unexpected), and only the exception class and HTTP status are logged, never the message text
- Versioned SQLite store for review results; results survive restarts
- Webhook idempotency: a `review_claims` table with `delivery_id` as primary key and a UNIQUE constraint on `(installation, repository, PR, head SHA)`; `claim_review` decides the winner atomically, duplicates get `202` with `status: "duplicate"`, and a missing `X-GitHub-Delivery` is rejected with `400`
- Claim lifecycle: `queued` -> `running` -> `completed` or `failed`; a `failed` claim can be taken over by a later delivery of the same commit, so a failed review is retried on redelivery

### Evaluation harness

Files: `evals/` (guide in `evals/README.md`)

- TOML case format with expected findings; `make_case` builds diffs from before/after files
- Runner uses the real `ReviewService` with a local GitHub stand-in
- Scoring: file + side + ±3-line matching, one-to-one; precision, recall, F1, clean-PR false-positive rate, location and category agreement
- Markdown and JSON reports with dataset, prompt hash, model, and git SHA, plus an error-analysis section
- Provider rate-limit handling in evals (`evals/ratelimit.py`)
- `v1` dataset: 30 cases (20 dev, 10 held-out), bug and clean cases
- First dev baseline (`evals/reports/20261018-075131-dev-qwen-qwen3.8-27b.md`): qwen3.8-27b on Groq, 3 repeats, precision 0.84, recall 0.76, F1 0.79, clean false-positive rate 0.15. Misses are mostly flaky empty model outputs (weakest: `race_condition_lost_update`); false positives are speculative findings on correct code; 3 reviews failed with `provider_error`, cause not yet confirmed
- `make eval`, `make eval-fake`; CI runs a fake-provider smoke run

### Tooling

- **uv** with `uv.lock` and a `dev` dependency group
- **ruff** for lint and formatting, **pyright** for type checking
- **pre-commit** hooks: ruff, pyright, gitleaks, file hygiene
- **GitHub Actions** CI (`.github/workflows/ci.yml`) running `make check` steps plus gitleaks
- **Dependabot** for weekly uv and Actions updates

```bash
make install    # uv sync
make dev
make check      # what CI runs
make hooks      # install pre-commit hooks
```

Tests cover config, webhook signatures and dispatch, GitHub auth and client behavior, diff parser fixtures, review schemas, review API auth, review budget and failures, provider factory, and the SQLite store.

## Known gaps

1. **Stale claims.** A claim stuck in `queued` or `running` (for example after a crash) is never reclaimed, so that commit is not reviewed again until a new commit arrives. Needs a staleness rule (claims older than N minutes may be taken over). `review_id` is also not yet stored on the claim, so a duplicate response cannot point to the existing review.
2. **No durable job queue.** Webhook reviews run in FastAPI `BackgroundTasks`; in-progress work is lost on restart. `POST /reviews` runs the review inline within the request.
3. **Eval baseline is dev-only.** The held-out split has not been run. The baseline's `provider_error` failures were traced to the Groq tier's 1,000 output-tokens-per-minute limit, but the empty model outputs are not yet explained. `tictactoe_final_design` may be mislabelled as clean. `LLM_MODEL` in `.env` has a trailing comma.
4. **Idempotency tests are incomplete.** Only `test_first_claim_wins` exists. The remaining store and webhook tests, and tests for the claim lifecycle, are listed in `IDEMPOTENCY_PLAN.md`.
5. **No threat model or adversarial tests.**
6. **Unused settings.** `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `CHROMA_PERSIST_DIR`, and `EMBEDDING_MODEL` exist in `src/config.py` but are not used yet.

## Next steps

1. **Idempotency follow-ups:** write the deferred store and webhook tests (concurrent claims first), add a staleness rule for stuck claims, and store `review_id` on the claim.
2. **Evaluation (milestone 3):** find out whether the `provider_error` failures are rate limits or timeouts, investigate the empty outputs, then run the held-out split once.
3. **Security docs and tests:** `docs/threat-model.md` and prompt-injection fixtures (do not reveal secrets, do not obey PR instructions, do not publish without approval).
4. **LangGraph (milestone 4):** `fetch -> normalize -> review -> verify -> aggregate -> save` with typed state, trace IDs, bounded retries, and no duplicate external effects.
5. **Focused RAG (milestone 5):** index Python files, tests, and docs; retrieve callers/tests for changed symbols; compare diff-only vs. RAG in evals.

## Change log

- **October 31, 2026 (webhooks):** Added webhook idempotency (`review_claims` table, atomic `claim_review`, duplicate responses, `400` on a missing delivery header) and the claim lifecycle (`queued`/`running`/`completed`/`failed`, with failed claims reclaimable). Test client now uses a throwaway database. Added `provider_rate_limited`, `provider_unavailable` and `provider_rejected` failure codes with status logging.
- **October 25, 2026 (evals):** Expanded the dataset to 30 cases (20 dev, 10 held-out), added the output format to the reviewer prompt and rate-limit waiting to evals, recorded the first dev baseline (qwen3.8-27b, 3 repeats), and filled in its error analysis.
- **October 10, 2026 (evals):** Added the evaluation harness in `evals/` with 3 example cases, scoring tests, `make eval` targets, and a CI smoke run.
- **October 10, 2026 (tooling):** Migrated to uv (removed `requirements.txt`, black replaced by ruff format), added pyright enforcement, pre-commit, `make check`, GitHub Actions CI, and Dependabot. Fixed two unchecked `fetchone()` results in `src/review/store.py`. Added the technology additions plan to `ROADMAP.md`.
- **October 10, 2026:** Renamed `src/llm/groq_proivder.py` to `src/llm/groq_provider.py`; renamed `ReviewStatus.QUEUD = "queud"` to `ReviewStatus.QUEUED = "queued"` (no stored rows used the old value). Marked milestone 2 complete in `ROADMAP.md`.
- **October 4, 2026:** Webhook wired to `ReviewService`, provider factory added, SQLite review store added.
