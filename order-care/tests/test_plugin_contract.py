from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest


@contextmanager
def _plugin_environment(plugin_root: Path, database: Path):
    previous_paths = os.environ.get("OPENMINION_PLUGIN_PATHS")
    previous_database = os.environ.get("ORDER_CARE_DB")
    os.environ["OPENMINION_PLUGIN_PATHS"] = str(plugin_root)
    os.environ["ORDER_CARE_DB"] = str(database)
    try:
        yield
    finally:
        if previous_paths is None:
            os.environ.pop("OPENMINION_PLUGIN_PATHS", None)
        else:
            os.environ["OPENMINION_PLUGIN_PATHS"] = previous_paths
        if previous_database is None:
            os.environ.pop("ORDER_CARE_DB", None)
        else:
            os.environ["ORDER_CARE_DB"] = previous_database


def _config(tmp_path: Path, plugin_id: str):
    from openminion.base.config import (
        AgentProfileConfig,
        OpenMinionConfig,
        save_config,
    )

    config = OpenMinionConfig()
    config.agents = {
        "openminion": AgentProfileConfig(
            name="openminion",
            provider="echo",
            default_channel="console",
        )
    }
    config.default_agent = "openminion"
    config.storage.path = str(tmp_path / "state.db")
    config.enabled_plugins = [plugin_id]
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    return config_path


def _copy_plugin(tmp_path: Path) -> Path:
    source = Path(__file__).parents[1] / "plugin"
    destination = tmp_path / "plugin"
    shutil.copytree(source, destination)
    return destination


@pytest.mark.dev_contract
def test_installed_dev_loads_canonical_plugin_and_enforces_policy(
    tmp_path: Path,
) -> None:
    from openminion import APIRuntime
    from openminion.tools import PLUGIN_MANIFEST_CONTRACT_VERSION, Plugin, ToolDecl

    assert Plugin is not None
    assert ToolDecl is not None
    assert PLUGIN_MANIFEST_CONTRACT_VERSION == "1"

    plugin_root = _copy_plugin(tmp_path)
    with _plugin_environment(plugin_root, tmp_path / "orders.db"):
        runtime = APIRuntime.from_config_path(
            str(_config(tmp_path, "example.order_care"))
        )
    try:
        tool_name = "plugin.example.order_care.apply_state"
        assert tool_name in runtime.tools.list()
        decision = runtime.action_policy.check(
            {"tool": tool_name, "args": {"order_id": "A-1", "state": "shipped"}},
            {},
        )
        assert decision.decision == "REQUIRE_CONFIRM"
    finally:
        runtime.close()


def _update_manifest(plugin_root: Path, **changes: str) -> None:
    manifest_path = plugin_root / "order_care.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(changes)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.dev_contract
def test_installed_dev_rejects_plugin_contract_version(tmp_path: Path) -> None:
    from openminion import APIRuntime

    plugin_root = _copy_plugin(tmp_path)
    _update_manifest(plugin_root, contract_version="2")

    with _plugin_environment(plugin_root, tmp_path / "orders.db"):
        with pytest.raises(RuntimeError, match="contract_version"):
            APIRuntime.from_config_path(str(_config(tmp_path, "example.order_care")))


@pytest.mark.dev_contract
def test_installed_dev_rejects_plugin_namespace_mismatch(tmp_path: Path) -> None:
    from openminion import APIRuntime

    plugin_root = _copy_plugin(tmp_path)
    _update_manifest(plugin_root, id="example.other")

    with _plugin_environment(plugin_root, tmp_path / "orders.db"):
        with pytest.raises(RuntimeError, match="Plugin activation failed") as caught:
            APIRuntime.from_config_path(str(_config(tmp_path, "example.other")))

    assert getattr(caught.value, "reason_code", "") == "owner_mismatch"
