.PHONY: install dev test lint format clean

# Install dependencies into a virtualenv
install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

# Run the FastAPI dev server with hot-reload
dev:
	.venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

# Run all tests with coverage
test:
	.venv/bin/pytest tests/ -v --cov=src --cov-report=term-missing

# Lint with ruff
lint:
	.venv/bin/ruff check src/ tests/

# Format with black
format:
	.venv/bin/black src/ tests/

# Remove build artifacts and caches
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -name "*.pyc" -delete
	rm -rf .coverage htmlcov/
