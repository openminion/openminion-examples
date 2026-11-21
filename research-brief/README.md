# Research brief

Creates a cited JSON artifact with read-only agent execution, a fixed provider
call budget, deterministic citation checks, and application-owned request to
session mapping. Restarting the process preserves the mapping and supports a
new follow-up turn; it does not reattach an in-flight stream.

```bash
python -m pip install -e ".[dev]"
research-brief "What changed in Python packaging this year?"
pytest
```

Set `OPENMINION_LIVE_CONFIG` and run `pytest -m live` for an opt-in configured
provider smoke.
