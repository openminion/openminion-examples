import json
import os
from pathlib import Path

import pytest

from order_care.app import OpenMinionOrderRuntime, OrderCareWorkflow, OrderStore


@pytest.mark.live
def test_live_approval_and_terminal_state(tmp_path: Path, monkeypatch) -> None:
    source_config = os.getenv("OPENMINION_LIVE_CONFIG")
    if not source_config:
        pytest.skip("OPENMINION_LIVE_CONFIG is not set")

    config = json.loads(Path(source_config).read_text(encoding="utf-8"))
    enabled = list(config.get("enabled_plugins", []))
    config["enabled_plugins"] = [*enabled, "example.order_care"]
    config_path = tmp_path / "agents.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    app_db = tmp_path / "orders.db"
    plugin_root = Path(__file__).parents[1] / "plugin"
    monkeypatch.setenv("OPENMINION_PLUGIN_PATHS", str(plugin_root))
    monkeypatch.setenv("ORDER_CARE_DB", str(app_db))

    store = OrderStore(app_db)
    store.put_order("order-1", "processing")
    runtime = OpenMinionOrderRuntime(str(config_path))
    try:
        workflow = OrderCareWorkflow(store, runtime)
        pending = workflow.prepare("action-1", "order-1", "cancelled")
        assert pending.status == "pending"
        assert pending.approval_id

        completed = workflow.decide(
            pending.action_id, pending.approval_id, "allow_once"
        )
        assert completed.status == "applied"
        assert store.order_state("order-1") == "cancelled"
    finally:
        runtime.close()
