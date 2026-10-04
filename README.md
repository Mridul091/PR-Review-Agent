# PR Reviewer Agent

A FastAPI service that accepts GitHub pull-request webhooks and prepares repository changes for an AI-assisted review pipeline.

The current service provides secure webhook ingestion, GitHub App authentication, paginated PR-file retrieval, bounded diff parsing, a Python-focused LLM reviewer, and SQLite-backed review result storage. LangGraph, RAG, durable background job recovery, and GitHub publishing are planned in [ROADMAP.md](ROADMAP.md).

## Current flow

```text
GitHub webhook
  -> payload-size check
  -> HMAC-SHA256 signature verification
  -> pull_request payload validation
  -> background review task
  -> SQLite-persisted review result

GitHub client
  -> installation-scoped authentication
  -> PR metadata with target/head repository identity
  -> paginated file metadata or bounded raw diff
  -> normalized files, hunks, and old/new line numbers
```

## Requirements

- Python 3.11+
- A GitHub App for live GitHub requests

## Setup

```bash
cp .env.example .env
make install
make dev
```

The API runs at `http://localhost:8000`. OpenAPI documentation is available at `/docs`, and `GET /health` reports basic service configuration.

Configure these values in `.env`:

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `development`, `test`, or `production` |
| `GITHUB_APP_ID` | GitHub App identifier |
| `GITHUB_PRIVATE_KEY_PATH` | Path to the GitHub App PEM key |
| `GITHUB_WEBHOOK_SECRET` | HMAC secret; mandatory in production |
| `REVIEW_API_TOKEN` | Bearer token for the review API |
| `REVIEW_REPOSITORY_ACCESS` | JSON mapping installation IDs to target repositories allowed for that token; defaults to no access |
| `MAX_PR_FILES` | Maximum files accepted in one review |
| `MAX_DIFF_BYTES` | Maximum aggregate diff bytes |
| `MAX_REVIEW_CONTEXT_CHARS` | Maximum formatted Python diff context sent to the model; oversize reviews return a rejected result without calling the model |
| `MAX_MODEL_ATTEMPTS` | Total model attempts including transient retries (default 2, maximum 3); one elapsed-time limit covers all attempts |
| `MAX_PATCH_BYTES` | Maximum patch bytes for one file |
| `MAX_WEBHOOK_BYTES` | Maximum webhook request body |
| `GITHUB_REQUEST_TIMEOUT_SECONDS` | Timeout for each GitHub HTTP request |
| `LLM_PROVIDER` | `fake` for deterministic local/test responses or `groq` for real model calls; `fake` is rejected in production |
| `LLM_MODEL` | Model name passed to the selected provider |
| `GROQ_API_KEY` | Required only when `LLM_PROVIDER=groq` |
| `DATABASE_URL` | File-backed SQLite database URL for persisted review results (default: `sqlite:///./pr_reviewer.db`; `:memory:` is unsupported) |

Never commit `.env`, private keys, or API credentials.

## Review API authorization

Set `REVIEW_REPOSITORY_ACCESS='{"1234":["owner/repo"]}'` for a GitHub App installation ID and target repository. `POST /api/v1/reviews` requires `Authorization: Bearer <REVIEW_API_TOKEN>` and authorizes both the supplied `installation_id` and `repository` before any review work starts. `GET /api/v1/reviews/{review_id}` rechecks access against the stored review; unauthorized or unknown IDs return 404. Repository names are case-insensitive. Missing or empty allowlists deny all review requests. This single-token configuration gives every token holder the same access; it does not establish separate user identities. Review results are persisted in SQLite and survive application restarts. Background execution itself is not durable: work in progress can be lost if the process exits. The review response includes typed `failures` for GitHub ingestion, malformed model output, provider timeouts, and exhausted retries. Only transient model errors are retried; `MAX_MODEL_ATTEMPTS` and one overall model deadline bound those calls. Failed reviews do not publish to GitHub.

## GitHub webhook

Configure the GitHub App to send pull-request events to:

```text
POST /api/v1/webhook
```

The service schedules reviews for `opened`, `synchronize`, and `reopened`. Other event/action combinations return an `ignored` response. Reviewable payloads must contain:

- `action`
- `repository.full_name`
- `pull_request.number`
- `installation.id`

In production, application startup fails when `GITHUB_WEBHOOK_SECRET` is absent. Development and test environments may omit it for local work and emit a warning.

## Validation

```bash
make test
make lint
```

Diff fixtures cover added, modified, deleted, renamed, binary, empty, multi-hunk, header-like source content, and oversized input behavior. GitHub client tests use an in-process mock transport and never call GitHub.

## Current limitations

- Webhook-triggered reviews run in FastAPI background tasks; queued/in-progress work is not recoverable after a process restart.
- SQLite stores completed review results, but webhook delivery/review idempotency is not implemented.
- Missing GitHub `patch` fields are surfaced as unavailable because GitHub does not reliably distinguish binary content from omitted large patches in that response.
- No findings are published to GitHub yet.
- RAG, evaluations, and LangGraph orchestration remain future milestones.
