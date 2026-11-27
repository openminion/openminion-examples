# Research brief

Creates a cited JSON artifact with read-only agent execution, a bounded turn
timeout, deterministic citation checks, and application-owned request to
session mapping. The requested tool-call count is a model instruction; the
runtime profile remains the owner of enforced tool budgets. Restarting the
process preserves the mapping and supports a real OpenMinion follow-up turn; it
does not reattach an in-flight stream.

```bash
python -m pip install -e ".[dev]"
export OPENMINION_CONFIG=/path/to/your/openminion-config   # or pass --config
research-brief "What changed in Python packaging this year?"
pytest
```

Set `OPENMINION_LIVE_CONFIG` and run `pytest -m live` for an opt-in configured
provider smoke.
