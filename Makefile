.PHONY: install dev test lint format format-check typecheck check hooks clean

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
	uv run ruff check src/ tests/

# Format with ruff and apply safe lint fixes
format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

# Fail if any file needs formatting
format-check:
	uv run ruff format --check src/ tests/

# Type check with pyright
typecheck:
	uv run pyright

# Everything CI runs
check: lint format-check typecheck
	uv run pytest tests/ -q

# Install the git pre-commit hooks
hooks:
	uv run pre-commit install

# Remove build artifacts and caches
clean:
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -not -path "./.venv/*" -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov/
