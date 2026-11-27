from pathlib import Path

import pytest
from openminion.api import TurnChunk

from order_care.app import (
    OpenMinionOrderRuntime,
    OrderCareWorkflow,
    OrderStore,
    PreparedApproval,
)


class FakeRuntime:
    def __init__(self, store: OrderStore, *, apply_effect: bool = True) -> None:
        self.store = store
        self.apply_effect = apply_effect
        self.resolutions: list[tuple[str, str, str]] = []
        self.reads = 0
        self.effects: dict[str, tuple[str, str]] = {}

    def prepare(
        self, order_id: str, requested_state: str, *, session_id: str
    ) -> PreparedApproval:
        trace_id = f"trace-{order_id}"
        self.effects[trace_id] = (order_id, requested_state)
        return PreparedApproval(trace_id, f"approval-{requested_state}")

    def resolve(self, trace_id: str, approval_id: str, decision: str) -> None:
        self.resolutions.append((trace_id, approval_id, decision))
        if decision == "allow_once" and self.apply_effect:
            order_id, requested_state = self.effects[trace_id]
            self.store.put_order(order_id, requested_state)

    def read_order(self, order_id: str, *, session_id: str) -> str:
        self.reads += 1
        return f"{order_id}: {self.store.order_state(order_id)}"


def _workflow(tmp_path: Path) -> tuple[OrderCareWorkflow, OrderStore, FakeRuntime]:
    store = OrderStore(tmp_path / "orders.db")
    store.put_order("A-1", "paid")
    runtime = FakeRuntime(store)
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


def test_mismatched_approved_effect_is_not_recorded_as_applied(tmp_path: Path) -> None:
    store = OrderStore(tmp_path / "orders.db")
    store.put_order("A-1", "paid")
    workflow = OrderCareWorkflow(store, FakeRuntime(store, apply_effect=False))
    pending = workflow.prepare("action-1", "A-1", "shipped")

    with pytest.raises(RuntimeError, match="did not match"):
        workflow.decide("action-1", pending.approval_id, "allow_once")

    assert store.order_state("A-1") == "paid"
    assert store.action("action-1").status == "uncertain"


def test_prepare_ignores_additive_stream_event_kinds() -> None:
    class Handle:
        trace_id = "trace-1"

        def stream(self):
            yield TurnChunk(trace_id=self.trace_id, kind="future_kind", sequence=1)
            yield TurnChunk(
                trace_id=self.trace_id,
                kind="approval",
                sequence=2,
                data={"phase": "requested", "approval_id": "approval-1"},
            )

    class Runtime:
        def submit_turn(self, *, payload: dict[str, object]):
            return Handle()

    runtime = OpenMinionOrderRuntime.__new__(OpenMinionOrderRuntime)
    runtime.runtime = Runtime()
    runtime.handles = {}

    prepared = runtime.prepare("A-1", "shipped", session_id="session-1")

    assert prepared == PreparedApproval("trace-1", "approval-1")
