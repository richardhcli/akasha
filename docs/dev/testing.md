# Testing

Tiers and CI gates are specified once in [`../mvp-spec.md`](../mvp-spec.md) §6 — not repeated here. Quick map to commands:

```bash
uv run pytest tests/unit tests/property        # make check's test leg -- required before any task is DONE
uv run pytest tests/golden                      # byte-exact fixtures; never hand-edit expected output (see CLAUDE.md rule 3)
uv run pytest tests/integration                 # temp vault + live daemon on a random port
uv run pytest tests/battery                     # make battery -- required before closing any M5+ task
uv run pytest tests/integration/test_ui_smoke.py tests/integration/test_concurrency.py   # needs `playwright install --with-deps chromium` first
```

`uv run ruff check src tests && uv run pyright src && uv run pytest tests/unit tests/property` is `make check`; run it before considering any change done.

## Rules that affect how you write tests

- Never edit `tests/golden/**` to make an implementation pass — golden files change only via a task that explicitly says so (`CLAUDE.md` rule 3).
- The OpenAPI snapshot ([`../api-snapshot/openapi.json`](../api-snapshot/openapi.json)) is regenerated only via the sanctioned command documented in `tests/integration/test_openapi_snapshot.py`'s module docstring — never hand-edited.
- `pickle`, `eval`, `exec` are banned everywhere, enforced by `tests/unit/test_no_pickle_ban.py` and ruff.
