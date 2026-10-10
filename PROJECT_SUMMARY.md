# Project Summary

Status snapshot of the PR Reviewer Agent: scope, progress, gaps, and next steps. Last updated October 10, 2026.

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
| 3. Baseline evaluation | Not started |
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
- Typed failures for GitHub, input, and model errors
- Versioned SQLite store for review results; results survive restarts

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

1. **No webhook idempotency.** `X-GitHub-Delivery` is only logged, so redelivered events create duplicate reviews.
2. **No durable job queue.** Webhook reviews run in FastAPI `BackgroundTasks`; in-progress work is lost on restart. `POST /reviews` runs the review inline within the request.
3. **No evaluation harness.** Review quality is not measured yet.
4. **No threat model or adversarial tests.**
5. **Unused settings.** `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `CHROMA_PERSIST_DIR`, and `EMBEDDING_MODEL` exist in `src/config.py` but are not used yet.

## Next steps

1. **Webhook idempotency:** persist delivery IDs and review identity `(installation, repository, PR, head SHA)` with a UNIQUE constraint in SQLite; test duplicate deliveries.
2. **Evaluation harness (milestone 3):** `evals/datasets/` with 5–10 synthetic Python PR cases to start, expected findings, and a runner measuring precision, recall, clean-PR false positives, and location accuracy. Record the diff-only baseline.
3. **Security docs and tests:** `docs/threat-model.md` and prompt-injection fixtures (do not reveal secrets, do not obey PR instructions, do not publish without approval).
4. **LangGraph (milestone 4):** `fetch -> normalize -> review -> verify -> aggregate -> save` with typed state, trace IDs, bounded retries, and no duplicate external effects.
5. **Focused RAG (milestone 5):** index Python files, tests, and docs; retrieve callers/tests for changed symbols; compare diff-only vs. RAG in evals.

## Change log

- **October 10, 2026 (tooling):** Migrated to uv (removed `requirements.txt`, black replaced by ruff format), added pyright enforcement, pre-commit, `make check`, GitHub Actions CI, and Dependabot. Fixed two unchecked `fetchone()` results in `src/review/store.py`. Added the technology additions plan to `ROADMAP.md`.
- **October 10, 2026:** Renamed `src/llm/groq_proivder.py` to `src/llm/groq_provider.py`; renamed `ReviewStatus.QUEUD = "queud"` to `ReviewStatus.QUEUED = "queued"` (no stored rows used the old value). Marked milestone 2 complete in `ROADMAP.md`.
- **October 4, 2026:** Webhook wired to `ReviewService`, provider factory added, SQLite review store added.
