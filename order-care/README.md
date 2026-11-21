# Order care

An approval-gated order workflow. The application owns orders and action
records; OpenMinion owns turn execution, policy, and approval events.

The app records `allow_once` or `deny`, rejects stale or conflicting decisions,
reads the order after an allowed effect, and marks an interrupted execution
`uncertain` after restart. It never automatically replays an uncertain action.

```bash
python -m pip install -e ".[dev]"
pytest
```

The included local manifest plugin owns the two domain tools. Add
`example.order_care` to `enabled_plugins`, then point discovery and the tool at
the application database:

```bash
export OPENMINION_PLUGIN_PATHS="$PWD/plugin"
export ORDER_CARE_DB="$PWD/var/orders.db"
```

Set `OPENMINION_LIVE_CONFIG` and run `pytest -m live` for an opt-in MiniMax
smoke that reaches a real pending approval, resolves `allow_once`, and verifies
the terminal application state.
