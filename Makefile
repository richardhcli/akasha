.PHONY: fmt check battery run

fmt:
	uv run ruff format .

check:
	uv run ruff check src tests
	uv run pyright src
	uv run pytest tests/unit tests/property

battery:
	uv run pytest tests/battery

run:
	uv run python -m akasha.cli.main daemon
