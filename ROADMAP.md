# PR Reviewer Agent: Next Steps Roadmap

Prepared September 11, 2026 from the current repository source. Status updated October 10, 2026.

## Outcome

Build a repository-aware PR reviewer demonstrating LangGraph orchestration, retrieval-augmented generation (RAG), reproducible evaluations, and enforced agent-security controls. The portfolio deliverable is a working review API, a benchmark report, an adversarial test suite, and a short reproducible demo.

This roadmap updates the priorities in `implementation_plan_revised.md`. It moves baseline evaluation before RAG and defers additional specialist agents and a dashboard until the core reviewer is measured. Checked items are implemented; unchecked items are planned. See `PROJECT_SUMMARY.md` for a status snapshot.

## Current implementation

| Area | Source evidence | Status |
|---|---|---|
| API | `src/main.py`, `src/api/review.py`, `src/api/webhooks.py` | `GET /health`, `POST /api/v1/webhook`, `POST /api/v1/reviews`, `GET /api/v1/reviews/{review_id}`; webhook and review API share `ReviewService` |
| Authentication | `src/github/auth.py`, `src/middleware/auth.py` | GitHub App JWT/token exchange, installation-scoped token cache; bearer-token review API with installation/repository allowlist |
| GitHub integration | `src/github/client.py` | PR metadata with target/fork identity, paginated files, bounded raw diff, file content; publishing methods exist but are not wired |
| Diff parsing | `src/github/diff_parser.py` | Files, hunks, old/new line numbers; fixtures cover added, modified, deleted, renamed, binary, empty, multi-hunk, and oversized diffs |
| Webhook security | `src/api/middleware.py`, `src/config.py` | HMAC verification, payload-size limit, payload validation; secret mandatory in production |
| AI pipeline | `src/llm/`, `src/agents/`, `src/review/service.py` | Provider factory (`fake`, `groq`), Python bug detector, schema-validated findings with diff-evidence and line checks, bounded retries and deadline |
| Storage | `src/review/store.py` | Versioned SQLite review-result store; no delivery idempotency or durable job queue |
| Tests | `tests/` | 88 tests passing with fake GitHub transport and fake LLM provider |
| Not started | — | Evaluation runner, LangGraph workflow, retrieval index, threat model, publishing, CI |

## Milestone 1 — Reliable input and review contracts

Priority: next work session. Dependency: none.

- [x] Create `src/review/schemas.py` with ReviewRequest, Finding, Evidence, and ReviewResult models. Include repository identity, installation ID, base/head commit SHA, file/line/side, evidence references, failures, timing, and usage. Do not place credentials in model-visible state.
- [x] Add fixtures and tests for added, modified, deleted, renamed, binary, empty, multi-hunk, and oversized diffs.
- [x] Fix deleted-file paths: the current parser uses `/dev/null` as the path. Preserve both old and new paths and reset state at file boundaries. Cover binary-only sections and source lines resembling diff headers.
- [x] Add pagination to `get_pr_files`; it currently requests one page. Explicitly surface incomplete/truncated input.
- [x] Correct repository identity for fork PRs: `get_pull_request` currently takes `repo_full_name` from the head repository. Keep the target repository separate from the fork repository and handle missing head repositories.
- [x] Enforce file, byte, and request limits; add typed failure results and mock HTTP tests.
- [x] Require a webhook secret outside explicit local development, validate payload structure, and test missing/invalid/valid signatures.
- [x] Add a README documenting the current capabilities, setup, and local checks.

Done when: fixture ingestion produces correct locations and repository identities; unsupported or oversized inputs have explicit outcomes; production configuration cannot silently disable signature verification.

## Milestone 2 — One reviewer working end to end

Dependency: milestone 1.

- [x] Add a provider interface in `src/llm/` with one real implementation and a deterministic fake for tests.
- [x] Build one bug reviewer in `src/agents/bug_detector.py`; start with Python PRs to bound scope.
- [x] Require structured findings: defect, trigger, impact, evidence, location, and suggested correction. An empty finding list is valid.
- [x] Implement `src/review/service.py`: fetch → normalize → budget → review → validate → return.
- [x] Validate file paths and line references against the reviewed snapshot; distinguish evidence elsewhere in the repository from publishable diff locations.
- [x] Bound transient retries, model calls, output size, and elapsed time. Return explicit failures for malformed output and provider errors.
- [x] Add `POST /api/v1/reviews` and `GET /api/v1/reviews/{review_id}` with caller authentication and repository authorization before public exposure.
- [x] Connect the existing webhook placeholder to the same service. Use mocked providers for integration tests.

Done when: a fixture PR produces validated findings through the API, a clean PR can return none, and provider failure is visible without posting anything to GitHub.

## Milestone 3 — Baseline evaluation before optimization

Dependency: milestone 2. Begin collecting fixtures during milestone 1.

- [ ] Create `evals/datasets/`, a dataset schema, an evaluation runner, and a versioned report format.
- [ ] Assemble 30–50 manually checked cases initially: labeled bugs, clean changes, context-dependent bugs, and ambiguous changes. Use public or synthetic code suitable for sharing.
- [ ] Split development and held-out cases before prompt tuning; keep variants of the same bug in the same split.
- [ ] Record expected bugs, acceptable evidence/locations, and issue-matching rules. Deduplicate predictions before scoring.
- [ ] Measure finding precision, labeled-bug recall, clean-PR false-positive rate, location accuracy, latency, token usage, and cost.
- [ ] Save dataset version, prompt/model configuration, and application revision with each experiment. Use human adjudication for disputed findings; an LLM judge is supplementary.
- [ ] Establish the diff-only baseline and publish both successful and failed examples.

Done when: one documented command runs the benchmark and generates a report with sample counts and clearly defined metrics. Set improvement targets after measuring this baseline; do not invent resume percentages.

## Milestone 4 — LangGraph state, verification, and recovery

Dependency: milestones 2–3.

Proposed graph:

`fetch → normalize → review → verify → aggregate → save result`

Later insert retrieval before review and an approval interrupt before publishing.

- [ ] Implement `src/review/graph.py` with typed state, node-level errors, and trace IDs. Reuse the independently testable service/reviewer components.
- [ ] Add a verifier that checks each finding against evidence and rejects or marks unsupported findings. Treat verifier decisions as fallible and evaluate them.
- [ ] Add bounded conditional retry/retrieval paths, avoiding open-ended loops.
- [ ] Introduce durable review storage and graph checkpoints; a local SQLite implementation is sufficient initially. (Review results are already persisted in SQLite; graph checkpoints remain.)
- [ ] Persist webhook delivery IDs and review identity `(installation, repository, PR, head SHA, configuration version)` to prevent duplicate work. Permit an explicit versioned rerun.
- [ ] Recover queued/interrupted work after restart; FastAPI BackgroundTasks alone is not a durable queue.
- [ ] Test retry, restart, stale-head detection, duplicate delivery, and verifier failure. Evaluate whether verification improves precision without unacceptable recall loss.

Done when: execution can recover from interruption and retries cannot create duplicate external effects. A graph trace explains how a finding reached the final result.

## Milestone 5 — Repository RAG with attributable evidence

Dependency: milestone 4 and a recorded baseline.

- [ ] Add `src/retrieval/` with indexing, search, and source metadata interfaces; choose one local vector store initially.
- [ ] Start with Python functions/classes, tests, and selected documentation. Combine symbol/keyword matching with semantic retrieval.
- [ ] Store installation ID, repository, commit SHA, path, symbol, and line range per chunk. Enforce access filters in code before returning results.
- [ ] Build context from explicit snapshots: trusted base-commit repository context plus clearly identified PR-head changes. Never silently mix current default-branch content with an older reviewed commit.
- [ ] Exclude secrets, binaries, generated/vendor files, and oversized content. Limit retrieval tokens and result count.
- [ ] Retrieve callers/tests for changed symbols; require findings to cite retrieved evidence.
- [ ] When retrieval is unavailable, mark the review as degraded and run a bounded diff-only fallback.
- [ ] Compare diff-only, RAG, and RAG-plus-verifier on the same held-out cases. Track retrieval recall@k on labeled evidence in addition to review metrics.

Done when: a cross-file bug is demonstrated with correct citations and the benchmark quantifies retrieval's benefit and cost. If RAG does not improve results, document the result and tune retrieval on the development split.

## Milestone 6 — Agent security and adversarial evaluation

Dependency: add basic boundaries from milestone 1; finish the suite after milestone 5.

- [ ] Write `docs/threat-model.md`: attacker-controlled PR text/code/docs, protected repository data and credentials, trusted application policies, available tools, and external effects.
- [ ] Add `src/security/` policy checks. Treat all retrieved content as data; do not let it choose repository permissions, tool capabilities, or publishing policy.
- [ ] Separate reviewer read access from publisher write access. Keep tokens, secrets, and unnecessary raw source out of prompts, persisted state, and routine logs.
- [ ] Replace raw authentication error-body logging with sanitized diagnostics in `src/github/auth.py`.
- [ ] Keep arbitrary shell execution out of the initial reviewer. If test execution is later added, use isolated disposable workers with no credentials and restricted network access.
- [ ] Create adversarial fixtures: instructions to approve a PR, hide a bug, expose a synthetic secret, retrieve a different installation's data, call an unauthorized tool, or exhaust the budget.
- [ ] Assert actual effects at the tool boundary: denied action, no unauthorized publication, no synthetic-secret disclosure, and no cross-repository retrieval.
- [ ] Report attack success per category and repeated-trial counts alongside normal review quality. No observed successes on a finite suite is not proof of immunity.

Done when: every exposed privileged capability has an enforced permission check, tests demonstrate the checks, and a reproducible security report records remaining limitations.

## Milestone 7 — Approval, publishing, and portfolio packaging

Dependency: milestones 4–6.

- [ ] Add a human approval step showing the exact proposed review and commit SHA. Recheck authorization and PR head before publication; a changed head invalidates approval.
- [ ] Reuse `GitHubClient.post_review` through a dedicated publisher. Start with neutral comments; use validated diff lines and an idempotency/reconciliation strategy for retries.
- [ ] Add CI checks for unit/integration tests and a small fixed regression suite. Run paid model benchmarks explicitly or on a controlled schedule.
- [ ] Publish a README architecture diagram, setup instructions, sample API response, benchmark report, threat model, limitations, and a 2–3 minute demo.
- [ ] Demonstrate three cases: a cross-file bug found with RAG, a false positive removed by verification, and a malicious instruction blocked by a policy control.
- [ ] Write resume bullets using measured counts and results, linked to reproducible evidence.

Done when: another developer can reproduce the demo from the README and inspect the evidence behind every resume claim.

## Technology additions

Added October 10, 2026. This is a personal learning project, so these additions favour widely used industry tools where they solve a real problem in this codebase. Each item names the milestone it supports.

### A. Developer tooling — before milestone 3

- [x] Manage the project with **uv** (`uv.lock`, dependency groups); remove the duplicated `requirements.txt`. (The unused local `venv/` directory still needs deleting by hand.)
- [x] Replace black with **ruff format**; keep ruff lint.
- [x] Enforce **pyright** type checking (`standard` mode; a few argument/attribute rules are relaxed for `tests/` only).
- [x] Add **pre-commit** hooks: ruff, ruff format, pyright, and **gitleaks** secret scanning.
- [x] Add `make check` (lint, format check, type check, tests) and a **GitHub Actions** workflow running it on every push and PR.
- [x] Add **Dependabot** for dependency and Actions updates.

### B. LLM layer — milestones 2–3

- [ ] Use provider-native **structured outputs** (JSON schema from the Pydantic models) instead of `json_object` mode and manual parsing; evaluate **Pydantic AI** or **Instructor**.
- [ ] Add a second real provider (Claude or OpenAI) behind `LLMProvider`, or a **LiteLLM** gateway; support model routing and fallback.
- [ ] Use **prompt caching** for the stable system prompt and repository context.
- [ ] Compute `estimated_cost_usd` from a per-model price table.
- [ ] Store prompts as versioned files and record the prompt version on each `ReviewResult`.

### C. Evaluation — milestone 3

- [ ] After the in-house runner works, adopt one framework: **promptfoo**, **DeepEval**, or **Ragas** (for retrieval in milestone 5).
- [ ] Add an **LLM-as-judge** finding matcher, calibrated against a hand-labelled subset.
- [ ] Run a small fixed regression eval in CI with the fake provider; run paid evals manually or on a schedule.

### D. Observability — milestones 3–4

- [ ] **OpenTelemetry** traces for each review stage (GitHub fetch, budget, model call, validation), with trace IDs in structlog output.
- [ ] LLM tracing with **Langfuse** (self-hostable) or **LangSmith** (pairs with LangGraph).
- [ ] **Prometheus** metrics (latency, failures by code, tokens per review) and a **Grafana** dashboard.

### E. Infrastructure and persistence — milestones 4–5

- [ ] **Docker** image and **Docker Compose** stack (app, Postgres, Redis, Langfuse).
- [ ] **PostgreSQL** with **SQLAlchemy 2.0 (async)** and **Alembic** migrations, replacing the hand-written SQLite migration logic; SQLite may remain for tests.
- [ ] **pgvector** as the vector store instead of Chroma.
- [ ] Durable execution to replace `BackgroundTasks`: a job queue (**ARQ**/**Celery** with Redis, or Postgres-backed **procrastinate**), or **LangGraph's Postgres checkpointer**.
- [ ] **Testcontainers** for integration tests against real Postgres and Redis.

### F. Agent architecture and RAG — milestones 4–5

- [ ] **LangGraph** with human-in-the-loop interrupts for publish approval.
- [ ] **tree-sitter** for function/class-level code chunking.
- [ ] Hybrid retrieval (BM25/symbol search plus embeddings) with a **reranker**; track recall@k.
- [ ] Expose the reviewer as an **MCP server** (`review_pr`, `get_review`) for MCP-capable clients.

### G. Security — milestone 6

- [ ] **Semgrep** or **Bandit** in CI; feed static-analysis results to the reviewer as additional evidence.
- [ ] Redact secrets from diffs before they reach the model.
- [ ] Map the threat model to the **OWASP Top 10 for LLM and agentic applications**.
- [ ] API rate limiting (`slowapi`) and per-installation quotas.

### H. Product surface — milestone 7

- [ ] Publish results through the **GitHub Checks API** with annotations.
- [ ] **Next.js** dashboard (`dashboard/` is already in `.gitignore`).
- [ ] Deploy the demo to a managed platform (Fly.io, Railway, or Cloud Run). Kubernetes and Terraform are optional.

### Not planned

Fine-tuning, microservices or Kafka, and additional specialist agents before evaluation identifies a specific gap.

## Immediate backlog, in order

Milestones 1 and 2 are complete. Next:

1. ~~Developer tooling (section A)~~ — done October 10, 2026.
2. Persist webhook delivery IDs and review identity `(installation, repository, PR, head SHA)` with database-level uniqueness so duplicate deliveries do not create duplicate reviews (pulled forward from milestone 4).
3. Milestone 3 evaluation harness with a CI regression run (sections C).
4. Docker Compose, Postgres, and Alembic (section E).
5. Structured outputs and a second provider (section B).
6. OpenTelemetry and Langfuse (section D).
7. LangGraph with a durable checkpointer (milestone 4, sections E–F).
8. RAG with tree-sitter and pgvector (milestone 5, section F).
9. Security suite (milestone 6, section G).
10. MCP server, Checks API publishing, and dashboard (milestone 7, sections F and H).

## Scope to defer

Defer five specialist agents, a dashboard, autonomous fixes/merging, historical-review memory, and distributed infrastructure. A second LLM provider is now in scope as a learning goal (section B). Add a second specialist only when evaluation reveals a specific gap. Store generated findings as review history, not trusted repository knowledge; this avoids feeding unsupported findings back into later reviews.

## Planning estimate

For one developer working part time, allow roughly 6–8 weeks, adjusting after the first milestone: 1–2 weeks for reliable input and a working reviewer, 1 week for evaluation, 1 week for LangGraph and recovery, 1–2 weeks for RAG, and 1–2 weeks for security, publishing, and documentation. These are planning estimates, not deadlines.

## Reference starting points

- RAG evaluation: https://docs.langchain.com/langsmith/evaluate-rag-tutorial
- Human approval workflows: https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- Agentic security risks: https://genai.owasp.org/2025/12/09/owasp-genai-security-project-releases-top-10-risks-and-mitigations-for-agentic-ai-security/

Verify current dependency APIs when implementing. This roadmap is based on source inspection; no live GitHub actions, model calls, or application changes were made.
