from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from openminion import Agent, APIRuntime
from pydantic import BaseModel

READ_ONLY_RESEARCH_TOOLS = ("web.search", "web.fetch")
TURN_TIMEOUT_SECONDS = 180


class Brief(BaseModel):
    title: str
    summary: str
    citations: list[str]


@dataclass(frozen=True)
class ResearchRequest:
    request_id: str
    topic: str
    session_id: str
    status: str
    artifact_path: str = ""


class ResearchClient(Protocol):
    def research(
        self, topic: str, *, session_id: str, max_calls_instruction: int
    ) -> Brief: ...

    def follow_up(self, question: str, *, session_id: str) -> str: ...


class OpenMinionResearch:
    def __init__(self, config_path: str, agent_id: str | None = None) -> None:
        self.runtime = APIRuntime.from_config_path(config_path)
        self.agent_id = agent_id

    def research(
        self, topic: str, *, session_id: str, max_calls_instruction: int
    ) -> Brief:
        prompt = (
            f"Research this topic using read-only tools: {topic}\n"
            f"Use at most {max_calls_instruction} tool calls. Return exact JSON with "
            "title, summary, and citations. citations must be a list of source URLs."
        )
        with Agent(
            runtime=self.runtime,
            agent_id=self.agent_id,
            session_id=session_id,
            tools=list(READ_ONLY_RESEARCH_TOOLS),
            output_type=Brief,
        ) as agent:
            return agent.run(prompt, timeout_seconds=TURN_TIMEOUT_SECONDS).output

    def follow_up(self, question: str, *, session_id: str) -> str:
        with Agent(
            runtime=self.runtime,
            agent_id=self.agent_id,
            session_id=session_id,
            tools=list(READ_ONLY_RESEARCH_TOOLS),
        ) as agent:
            return agent.run(question, timeout_seconds=TURN_TIMEOUT_SECONDS).text

    def close(self) -> None:
        self.runtime.close()


class ResearchStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS requests ("
                "request_id TEXT PRIMARY KEY, topic TEXT NOT NULL, "
                "session_id TEXT NOT NULL, status TEXT NOT NULL, "
                "artifact_path TEXT NOT NULL DEFAULT '')"
            )

    def create(self, request_id: str, topic: str) -> ResearchRequest:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO requests VALUES (?, ?, ?, 'pending', '')",
                (request_id, topic, uuid4().hex),
            )
        record = self.get(request_id)
        if record is None:
            raise RuntimeError("research request is missing")
        if record.topic != topic:
            raise ValueError("request_id already belongs to another topic")
        return record

    def get(self, request_id: str) -> ResearchRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_id, topic, session_id, status, artifact_path "
                "FROM requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return ResearchRequest(*row) if row else None

    def complete(self, request_id: str, artifact_path: Path) -> ResearchRequest:
        with self._connect() as connection:
            connection.execute(
                "UPDATE requests SET status = 'completed', artifact_path = ? "
                "WHERE request_id = ?",
                (str(artifact_path), request_id),
            )
        record = self.get(request_id)
        if record is None:
            raise RuntimeError("completed research request is missing")
        return record

    def fail(self, request_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE requests SET status = 'failed' WHERE request_id = ?",
                (request_id,),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class ResearchBriefWorkflow:
    def __init__(
        self,
        store: ResearchStore,
        client: ResearchClient,
        artifact_root: str | Path,
        *,
        max_calls_instruction: int = 8,
    ) -> None:
        self.store = store
        self.client = client
        self.artifact_root = Path(artifact_root)
        self.max_calls_instruction = max_calls_instruction

    def run(self, request_id: str, topic: str) -> ResearchRequest:
        request = self.store.create(request_id, topic)
        if request.status == "completed":
            return request
        try:
            brief = self.client.research(
                topic,
                session_id=request.session_id,
                max_calls_instruction=self.max_calls_instruction,
            )
            if not brief.citations or any(
                not item.startswith("http") for item in brief.citations
            ):
                raise ValueError("brief requires source URLs")
        except Exception:
            self.store.fail(request.request_id)
            raise
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        artifact = self.artifact_root / f"{request.request_id}.json"
        artifact.write_text(brief.model_dump_json(indent=2) + "\n", encoding="utf-8")
        Brief.model_validate_json(artifact.read_text(encoding="utf-8"))
        return self.store.complete(request.request_id, artifact)

    def follow_up(self, request_id: str, question: str) -> str:
        request = self.store.get(request_id)
        if request is None or request.status != "completed":
            raise ValueError("completed research request not found")
        return self.client.follow_up(question, session_id=request.session_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create one cited research brief.")
    parser.add_argument("topic")
    parser.add_argument("--config", default=os.getenv("OPENMINION_CONFIG"))
    parser.add_argument("--db", default="var/research.db")
    parser.add_argument("--artifacts", default="var/artifacts")
    args = parser.parse_args()
    if not args.config:
        parser.error("--config or OPENMINION_CONFIG is required")
    client = OpenMinionResearch(args.config)
    try:
        request = ResearchBriefWorkflow(
            ResearchStore(args.db), client, args.artifacts
        ).run(uuid4().hex, args.topic)
    finally:
        client.close()
    print(request.artifact_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
