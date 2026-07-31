.PHONY: fmt check check-fast battery run dev-ui

fmt:
	uv run ruff format .

# Full gate: unit + property + integration, including the [chromium]
# Playwright-driven UI tests (tests/integration/test_ui_*.py,
# test_ui_smoke.py) -- requires a real headless browser
# (`uv run playwright install chromium` once per environment). This is the
# holistic pre-task-done gate; debug-plan D10 was a T9.6-acceptance-test
# regression that sat undetected specifically because tests/integration was
# never part of this target before.
check:
	uv run ruff check src tests
	uv run pyright src
	uv run pytest tests/unit tests/property tests/integration

# Same as check, but deselects the [chromium] Playwright-driven UI tests --
# for environments with no headless-browser support (e.g. a minimal sandbox
# with no root to install Chromium's system deps; see debug-plan D9/D10's
# environment notes for a concrete example: `chrome-headless-shell: error
# while loading shared libraries: libXdamage.so.1`). Never a substitute for
# `make check` before closing a UI-touching task -- only a fallback when a
# real browser genuinely isn't available in the current environment.
check-fast:
	uv run ruff check src tests
	uv run pyright src
	uv run pytest tests/unit tests/property tests/integration -k "not chromium"

battery:
	uv run pytest tests/battery

run:
	uv run python -m akasha.cli.main daemon

dev-ui:
	uv run python scripts/dev/seed_and_run.py
