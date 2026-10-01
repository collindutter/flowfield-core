.PHONY: setup check format smoke build docs docs-serve api-types

setup:
	uv sync --locked
	pnpm --dir web install --frozen-lockfile
	pnpm --dir docs install --frozen-lockfile

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest
	uv run scripts/generate-api-types.py --check
	pnpm --dir web check
	pnpm --dir web build
	$(MAKE) docs

format:
	uv run ruff format .
	pnpm --dir web format

smoke:
	pnpm --dir web build
	pnpm --dir web test:e2e

build:
	pnpm --dir web build
	uv build

docs:
	pnpm --dir docs check

docs-serve:
	pnpm --dir docs dev

api-types:
	uv run scripts/generate-api-types.py
