sync:
	uv sync --all-groups

typecheck: sync
	uv run pyright

format: sync
	uv run ruff check --select I --fix .
	uv run ruff format .

format-check: sync
	uv run ruff check --select I .
	uv run ruff format --check .

lint: sync
	uv run ruff check .

lint-fix: sync
	uv run ruff check --fix .

install-dev: sync
	pre-commit install
