.PHONY: install dev test lint format format-check typecheck check hooks eval eval-fake clean

# Install dependencies (including the dev group) from uv.lock
install:
	uv sync

# Run the FastAPI dev server with hot-reload
dev:
	uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

# Run all tests with coverage
test:
	uv run pytest tests/ -v --cov=src --cov-report=term-missing

# Lint with ruff
lint:
	uv run ruff check src/ tests/ evals/

# Format with ruff and apply safe lint fixes
format:
	uv run ruff format src/ tests/ evals/
	uv run ruff check --fix src/ tests/ evals/

# Fail if any file needs formatting
format-check:
	uv run ruff format --check src/ tests/ evals/

# Type check with pyright
typecheck:
	uv run pyright

# Everything CI runs
check: lint format-check typecheck
	uv run pytest tests/ -q

# Run the eval suite with the configured LLM provider (calls the real model).
# Pass options with ARGS, e.g. make eval ARGS="--repeats 3 --split all"
eval:
	uv run python -m evals.run $(ARGS)

# Run the eval suite against the fake provider (free; checks the harness, not quality)
eval-fake:
	uv run python -m evals.run --provider fake --out $(or $(OUT),/tmp/pr-reviewer-evals) $(ARGS)

# Install the git pre-commit hooks
hooks:
	uv run pre-commit install

# Remove build artifacts and caches
clean:
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -not -path "./.venv/*" -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov/
