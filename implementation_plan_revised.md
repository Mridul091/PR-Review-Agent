# AI PR Reviewer Agent: Revised Implementation Plan

## 1. Goal

Build a reliable service that accepts a pull request, analyzes its diff with multiple specialized reviewers, and returns structured, actionable findings.

The first release should prioritize correctness, observability, and predictable failure handling over feature breadth.

## 2. Scope and Priorities

### MVP

- Submit a pull request by repository and pull request number.
- Authenticate with GitHub using an App or token-based client abstraction.
- Fetch pull request metadata, changed files, and patches.
- Run independent review agents against the diff.
- Aggregate and deduplicate findings.
- Return validated JSON findings through an API.
- Support webhook-triggered reviews after manual submission works.
- Provide logs, request IDs, timeouts, and clear partial-failure behavior.

### Post-MVP

- Persistent review history and user-facing dashboard.
- Repository-specific rules and configuration.
- Retrieval-augmented context from repository documentation.
- Inline GitHub review comments.
- Multiple LLM providers and model routing.
- Background job queue and distributed workers.

## 3. Proposed Architecture

```text
Client / GitHub Webhook
          |
          v
      FastAPI API
          |
          v
   Review Service Layer
          |
          +--> GitHub Client --> PR metadata and diff
          |
          +--> Review Agents
          |       +--> Security
          |       +--> Bug Detection
          |       +--> Code Quality
          |       +--> Performance
          |       +--> Test Coverage
          |
          v
   Finding Aggregator
          |
          v
   Schema-validated Review Result
```

Use LangGraph only where it provides real value: coordinating agent execution, retries, and shared state. Keep each agent callable and testable without the graph.

## 4. Core Contracts

Create stable models before implementing the agents.

### Review request

- `repository`: owner and repository name
- `pull_request_number`: positive integer
- `commit_sha`: optional verification value
- `requested_agents`: optional list

### Review finding

- `id`: deterministic identifier for deduplication
- `category`: security, bug, quality, performance, or testing
- `severity`: critical, high, medium, low, or info
- `confidence`: numeric value from 0 to 1
- `file_path`: changed file path
- `line_start` and `line_end`: optional changed-line locations
- `title`: short actionable summary
- `description`: explanation of the issue
- `recommendation`: suggested fix
- `agent`: producing agent name

### Review result

- request and pull request identifiers
- status: completed, partial, failed, or rejected
- findings
- agents attempted and failed
- duration and timestamps
- correlation/request ID

All model output must be parsed and validated against these schemas. Invalid output should be recorded as an agent failure, not returned as trusted findings.

## 5. Implementation Phases

### Phase 0: Repository and configuration foundation

- Confirm the project root is `/Users/mridulbagoria/Documents/PR-Reviewer-Agent`.
- Keep secrets in environment variables and provide `.env.example`.
- Add typed settings for GitHub, LLM, database, logging, and timeouts.
- Establish formatting, linting, type checking, and test commands.
- Add request ID and structured logging middleware.

Acceptance criteria:

- Application starts with documented environment variables.
- Missing required configuration fails with a clear message.
- Existing webhook tests continue to pass.

### Phase 1: GitHub integration and diff normalization

- Implement a GitHub client interface with a real implementation and fake test implementation.
- Verify webhook signatures using constant-time comparison.
- Support pull request metadata, changed files, patches, and file content where available.
- Normalize patches into reviewable units with path, change type, hunk headers, and changed lines.
- Enforce limits for file count, patch size, and request duration.

Acceptance criteria:

- Fixtures cover added, modified, deleted, renamed, binary, and empty-patch files.
- Invalid signatures are rejected.
- GitHub failures produce typed, testable errors.

### Phase 2: Single-agent review pipeline

- Implement the review service for one agent first, preferably bug detection.
- Create a provider-neutral LLM interface.
- Use structured prompts containing only relevant diff and context.
- Validate model responses against the finding schema.
- Add retries only for transient provider failures, with bounded timeouts.

Acceptance criteria:

- A fixture PR produces a deterministic normalized result in tests.
- Malformed model output is handled safely.
- Provider timeout and rate-limit behavior is covered.

### Phase 3: Multi-agent orchestration and aggregation

- Add the remaining agents behind the same interface.
- Execute agents independently, with configurable concurrency limits.
- Ensure one failed agent does not discard successful findings.
- Deduplicate findings using file, line range, category, and normalized title.
- Rank findings by severity and confidence.
- Add a minimum-confidence policy configurable per repository.

Acceptance criteria:

- The service returns `completed` when all agents succeed and `partial` when some fail.
- Duplicate findings are removed without hiding distinct issues.
- Agent execution and aggregation are unit tested separately.

### Phase 4: API and webhook workflow

- Add `POST /reviews` for manual review submission.
- Add `GET /reviews/{review_id}` for status and result retrieval.
- Add `POST /webhooks/github` for pull request events.
- Ignore unsupported actions and prevent duplicate processing of the same event.
- Use a background execution boundary once review latency exceeds the API timeout budget.

Acceptance criteria:

- API schemas are documented through OpenAPI.
- Duplicate webhook deliveries do not create duplicate reviews.
- End-to-end tests cover accepted, rejected, partial, and failed reviews.

### Phase 5: Persistence and operational readiness

- Add database models for repositories, review requests, review results, findings, and event deliveries.
- Store status transitions and failure reasons.
- Add retention guidance for diffs and model prompts because they may contain private source code.
- Add metrics for latency, token usage, agent failures, finding counts, and provider errors.

Acceptance criteria:

- Review status survives process restarts.
- Event delivery uniqueness is enforced at the database level.
- Sensitive values are absent from normal logs.

### Phase 6: Repository context and RAG

Add RAG only after baseline evaluation demonstrates that repository context improves results.

- Index selected files such as README files, contribution guides, and configuration documentation.
- Exclude secrets, generated files, vendored dependencies, binaries, and oversized files.
- Version or invalidate indexes when the default branch changes.
- Keep retrieved context bounded and identify its source files in the agent state.

Acceptance criteria:

- Retrieval tests verify relevant context selection.
- Indexing is repeatable and can be rebuilt.
- A review can run when the vector store is unavailable by falling back to diff-only context.

### Phase 7: Dashboard and GitHub publishing

- Build the dashboard after the API and persistence contracts stabilize.
- Show review status, severity filters, file locations, agent attribution, and failure details.
- Add optional inline GitHub comments only after finding line mapping is reliable.
- Make publishing idempotent and avoid posting duplicate comments.

Acceptance criteria:

- Dashboard behavior is covered at the API boundary.
- Findings link back to exact changed lines when possible.
- Users can distinguish model uncertainty from confirmed defects.

## 6. Security Requirements

- Verify webhook signatures before parsing or processing events.
- Use least-privilege GitHub permissions.
- Redact tokens, webhook secrets, prompts, and private source content from logs.
- Treat all repository content and PR text as untrusted input.
- Defend prompts against instruction injection from code comments, issue text, and documentation.
- Apply repository, file-size, and token-budget limits.
- Avoid storing diffs and prompts unless retention is explicitly configured.

## 7. Testing and Evaluation

Maintain three test layers:

- Unit tests for parsing, schemas, deduplication, signature verification, and configuration.
- Integration tests using fake GitHub and LLM providers.
- End-to-end tests for manual and webhook review flows.

Create a small labeled evaluation set containing:

- Realistic bug, security, performance, and testing issues.
- Clean changes that should produce no finding.
- Ambiguous changes where low confidence is expected.
- Large, renamed, deleted, binary, and generated files.

Track:

- Precision of actionable findings.
- Recall on known issues.
- Severity and line-location accuracy.
- False-positive rate on clean changes.
- End-to-end latency and provider cost.

Do not add more agents or RAG sources until the current evaluation results justify them.

## 8. Suggested Initial Project Structure

```text
src/
  api/
    routes.py
    webhooks.py
    middleware.py
  agents/
    base.py
    bug_detector.py
    security.py
    quality.py
    performance.py
    test_coverage.py
  github/
    auth.py
    client.py
    diff_parser.py
  llm/
    base.py
    provider.py
    schemas.py
  review/
    service.py
    orchestrator.py
    aggregator.py
  db/
    models.py
    repository.py
  config.py
  main.py
tests/
  fixtures/
  unit/
  integration/
  e2e/
```

## 9. Definition of Done for the First Release

- A manual review can be submitted and returns validated findings.
- GitHub webhook delivery can trigger the same workflow safely.
- Findings identify category, severity, confidence, file, and line information where available.
- Partial agent failures are visible and do not erase successful results.
- Secrets and private source content are protected in logs and storage.
- Unit, integration, and end-to-end tests cover the main success and failure paths.
- The README documents setup, environment variables, API usage, limitations, and local testing.

## 10. Recommended Build Order

1. Configuration, schemas, logging, and test fixtures.
2. GitHub client and diff parser.
3. One agent with a fake LLM provider.
4. Review service and aggregation.
5. Manual review API.
6. Remaining agents and orchestration.
7. Webhook processing and idempotency.
8. Persistence and metrics.
9. Evaluation improvements.
10. RAG, dashboard, and GitHub inline publishing.
