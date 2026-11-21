from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from openminion.api import APIRuntime, RuntimeTurnHandle

TURN_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class PreparedApproval:
    trace_id: str
    approval_id: str


@dataclass(frozen=True)
class OrderAction:
    action_id: str
    order_id: str
    requested_state: str
    session_id: str
    trace_id: str
    approval_id: str
    status: str
    decision: str = ""
    readback: str = ""


class OrderRuntime(Protocol):
    def prepare(
        self, order_id: str, requested_state: str, *, session_id: str
    ) -> PreparedApproval: ...

    def resolve(self, trace_id: str, approval_id: str, decision: str) -> None: ...

    def read_order(self, order_id: str, *, session_id: str) -> str: ...


class OpenMinionOrderRuntime:
    def __init__(self, config_path: str) -> None:
        self.runtime = APIRuntime.from_config_path(config_path)
        self.handles: dict[str, RuntimeTurnHandle] = {}

    def prepare(
        self, order_id: str, requested_state: str, *, session_id: str
    ) -> PreparedApproval:
        handle = self.runtime.submit_turn(
            payload={
                "message": (
                    f"Prepare and request approval to set order {order_id} "
                    f"to {requested_state}. Do not bypass policy."
                ),
                "session_id": session_id,
                "forced_tools": ["plugin.example.order_care.apply_state"],
                "deliver": False,
                "timeout_seconds": TURN_TIMEOUT_SECONDS,
            }
        )
        self.handles[handle.trace_id] = handle
        for chunk in handle.stream():
            if chunk.kind == "approval" and chunk.data.get("phase") == "requested":
                return PreparedApproval(
                    trace_id=handle.trace_id,
                    approval_id=str(chunk.data["approval_id"]),
                )
        raise RuntimeError("turn finished without an approval request")

    def resolve(self, trace_id: str, approval_id: str, decision: str) -> None:
        handle = self.handles.get(trace_id)
        if handle is None:
            raise RuntimeError("active approval handle is unavailable")
        if not handle.resolve_approval(approval_id=approval_id, decision=decision):
            raise ValueError("approval is stale or unknown")
        response = handle.result()
        if response.errors:
            raise RuntimeError(response.errors[0].message)
        self.handles.pop(trace_id, None)

    def read_order(self, order_id: str, *, session_id: str) -> str:
        response = self.runtime.run_turn(
            payload={
                "message": f"Read back the current state of order {order_id}.",
                "session_id": session_id,
                "forced_tools": ["plugin.example.order_care.read_state"],
                "deliver": False,
                "timeout_seconds": TURN_TIMEOUT_SECONDS,
            }
        )
        return str(response["body"])

    def close(self) -> None:
        self.runtime.close()


class OrderStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS orders ("
                "order_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS actions ("
                "action_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, "
                "requested_state TEXT NOT NULL, session_id TEXT NOT NULL, "
                "trace_id TEXT NOT NULL, approval_id TEXT NOT NULL, "
                "status TEXT NOT NULL, decision TEXT NOT NULL DEFAULT '', "
                "readback TEXT NOT NULL DEFAULT '')"
            )

    def put_order(self, order_id: str, state: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO orders(order_id, state) VALUES (?, ?) "
                "ON CONFLICT(order_id) DO UPDATE SET state = excluded.state",
                (order_id, state),
            )

    def order_state(self, order_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def add_action(
        self,
        action_id: str,
        order_id: str,
        requested_state: str,
        session_id: str,
        prepared: PreparedApproval,
    ) -> OrderAction:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO actions(action_id, order_id, requested_state, session_id, "
                "trace_id, approval_id, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (
                    action_id,
                    order_id,
                    requested_state,
                    session_id,
                    prepared.trace_id,
                    prepared.approval_id,
                ),
            )
        return self.action(action_id)

    def action(self, action_id: str) -> OrderAction:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT action_id, order_id, requested_state, session_id, trace_id, "
                "approval_id, status, decision, readback FROM actions "
                "WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        if row is None:
            raise ValueError("action not found")
        return OrderAction(*row)

    def set_action(
        self, action_id: str, *, status: str, decision: str, readback: str = ""
    ) -> OrderAction:
        with self._connect() as connection:
            connection.execute(
                "UPDATE actions SET status = ?, decision = ?, readback = ? "
                "WHERE action_id = ?",
                (status, decision, readback, action_id),
            )
        return self.action(action_id)

    def mark_interrupted_uncertain(self) -> int:
        with self._connect() as connection:
            return connection.execute(
                "UPDATE actions SET status = 'uncertain' WHERE status = 'executing'"
            ).rowcount

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class OrderCareWorkflow:
    def __init__(self, store: OrderStore, runtime: OrderRuntime) -> None:
        self.store = store
        self.runtime = runtime

    def prepare(
        self, action_id: str, order_id: str, requested_state: str
    ) -> OrderAction:
        if self.store.order_state(order_id) is None:
            raise ValueError("order not found")
        session_id = uuid4().hex
        prepared = self.runtime.prepare(
            order_id, requested_state, session_id=session_id
        )
        return self.store.add_action(
            action_id, order_id, requested_state, session_id, prepared
        )

    def decide(self, action_id: str, approval_id: str, decision: str) -> OrderAction:
        action = self.store.action(action_id)
        if action.decision:
            if action.approval_id == approval_id and action.decision == decision:
                return action
            raise ValueError("decision conflicts with the recorded decision")
        if action.status != "pending" or action.approval_id != approval_id:
            raise ValueError("approval is stale or unknown")
        if decision not in {"allow_once", "deny"}:
            raise ValueError("decision must be allow_once or deny")
        if decision == "deny":
            self.runtime.resolve(action.trace_id, approval_id, decision)
            return self.store.set_action(action_id, status="denied", decision=decision)

        self.store.set_action(action_id, status="executing", decision=decision)
        self.runtime.resolve(action.trace_id, approval_id, decision)
        readback = self.runtime.read_order(
            action.order_id, session_id=action.session_id
        )
        if self.store.order_state(action.order_id) != action.requested_state:
            self.store.set_action(
                action_id,
                status="uncertain",
                decision=decision,
                readback=readback,
            )
            raise RuntimeError("approved effect did not match the requested state")
        return self.store.set_action(
            action_id,
            status="applied",
            decision=decision,
            readback=readback,
        )
