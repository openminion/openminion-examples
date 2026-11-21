from pathlib import Path

import pytest

from order_care.app import OrderCareWorkflow, OrderStore, PreparedApproval


class FakeRuntime:
    def __init__(self) -> None:
        self.resolutions: list[tuple[str, str, str]] = []
        self.reads = 0

    def prepare(
        self, order_id: str, requested_state: str, *, session_id: str
    ) -> PreparedApproval:
        return PreparedApproval(f"trace-{order_id}", f"approval-{requested_state}")

    def resolve(self, trace_id: str, approval_id: str, decision: str) -> None:
        self.resolutions.append((trace_id, approval_id, decision))

    def read_order(self, order_id: str, *, session_id: str) -> str:
        self.reads += 1
        return f"{order_id}: shipped"


def _workflow(tmp_path: Path) -> tuple[OrderCareWorkflow, OrderStore, FakeRuntime]:
    store = OrderStore(tmp_path / "orders.db")
    store.put_order("A-1", "paid")
    runtime = FakeRuntime()
    return OrderCareWorkflow(store, runtime), store, runtime


def test_read_prepare_deny_and_duplicate_decision(tmp_path: Path) -> None:
    workflow, store, runtime = _workflow(tmp_path)
    assert store.order_state("A-1") == "paid"
    pending = workflow.prepare("action-1", "A-1", "cancelled")
    assert pending.status == "pending"

    denied = workflow.decide("action-1", pending.approval_id, "deny")
    duplicate = workflow.decide("action-1", pending.approval_id, "deny")

    assert denied == duplicate
    assert denied.status == "denied"
    assert runtime.reads == 0
    assert len(runtime.resolutions) == 1


def test_allow_once_reads_effect_and_rejects_conflict(tmp_path: Path) -> None:
    workflow, store, runtime = _workflow(tmp_path)
    pending = workflow.prepare("action-1", "A-1", "shipped")
    applied = workflow.decide("action-1", pending.approval_id, "allow_once")

    assert applied.status == "applied"
    assert applied.readback == "A-1: shipped"
    assert store.order_state("A-1") == "shipped"
    assert runtime.reads == 1
    with pytest.raises(ValueError, match="conflicts"):
        workflow.decide("action-1", pending.approval_id, "deny")


def test_stale_approval_and_restart_uncertainty_do_not_replay(tmp_path: Path) -> None:
    workflow, store, runtime = _workflow(tmp_path)
    workflow.prepare("action-1", "A-1", "shipped")
    with pytest.raises(ValueError, match="stale"):
        workflow.decide("action-1", "approval-stale", "allow_once")

    store.set_action("action-1", status="executing", decision="allow_once")
    restarted = OrderStore(store.path)
    assert restarted.mark_interrupted_uncertain() == 1
    assert restarted.action("action-1").status == "uncertain"
    assert runtime.resolutions == []
