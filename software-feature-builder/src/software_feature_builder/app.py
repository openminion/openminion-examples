from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from openminion import Agent, APIRuntime
from pydantic import BaseModel


class FeaturePlan(BaseModel):
    title: str
    file_path: str
    acceptance_criteria: list[str]


class CodingClient(Protocol):
    def plan(self, request: str, *, session_id: str) -> FeaturePlan: ...

    def implement(
        self,
        plan: FeaturePlan,
        *,
        repository: Path,
        session_id: str,
        verifier_output: str = "",
    ) -> None: ...


class OpenMinionCoding:
    def __init__(self, config_path: str, agent_id: str | None = None) -> None:
        self.runtime = APIRuntime.from_config_path(config_path)
        self.agent_id = agent_id

    def plan(self, request: str, *, session_id: str) -> FeaturePlan:
        with Agent(
            runtime=self.runtime,
            agent_id=self.agent_id,
            session_id=session_id,
            output_type=FeaturePlan,
        ) as agent:
            return agent.run(
                "Planning only: do not call tools or modify files. Return exact JSON "
                "with title, file_path, and acceptance_criteria for this bounded "
                f"disposable-repository request: {request}"
            ).output

    def implement(
        self,
        plan: FeaturePlan,
        *,
        repository: Path,
        session_id: str,
        verifier_output: str = "",
    ) -> None:
        prompt = (
            f"Implement {plan.title} in {plan.file_path} under {repository}. "
            f"Acceptance criteria: {plan.acceptance_criteria}. "
        )
        if verifier_output:
            prompt += f"The verifier failed with: {verifier_output}"
        target = (repository / plan.file_path).resolve()

        def approve_planned_write(
            tool_name: str, args: dict[str, object], approval_id: str
        ) -> bool:
            del approval_id
            raw_path = Path(str(args.get("path", "")))
            candidate = raw_path if raw_path.is_absolute() else repository / raw_path
            return tool_name == "file.write" and candidate.resolve() == target

        with Agent(
            runtime=self.runtime,
            agent_id=self.agent_id,
            session_id=session_id,
            forced_tools=["file.write"],
        ) as agent:
            agent.run(prompt, approval_callback=approve_planned_write)

    def close(self) -> None:
        self.runtime.close()


class FeatureStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS features ("
                "feature_id TEXT PRIMARY KEY, request TEXT NOT NULL, "
                "repository TEXT NOT NULL, session_id TEXT NOT NULL, "
                "status TEXT NOT NULL, plan_json TEXT NOT NULL DEFAULT '', "
                "attempts INTEGER NOT NULL DEFAULT 0, "
                "commit_sha TEXT NOT NULL DEFAULT '')"
            )

    def create(self, feature_id: str, request: str, repository: Path) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO features"
                "(feature_id, request, repository, session_id, status) "
                "VALUES (?, ?, ?, ?, 'requested')",
                (feature_id, request, str(repository.resolve()), uuid4().hex),
            )

    def get(self, feature_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT feature_id, request, repository, session_id, status, "
                "plan_json, "
                "attempts, commit_sha FROM features WHERE feature_id = ?",
                (feature_id,),
            ).fetchone()
        if row is None:
            raise ValueError("feature not found")
        keys = (
            "feature_id",
            "request",
            "repository",
            "session_id",
            "status",
            "plan_json",
            "attempts",
            "commit_sha",
        )
        return dict(zip(keys, row, strict=True))

    def update(self, feature_id: str, **values: object) -> None:
        allowed = {"status", "plan_json", "attempts", "commit_sha"}
        if not values or not set(values) <= allowed:
            raise ValueError("unsupported feature update")
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE features SET {assignments} WHERE feature_id = ?",  # noqa: S608
                (*values.values(), feature_id),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class FeatureBuilder:
    def __init__(self, store: FeatureStore, client: CodingClient) -> None:
        self.store = store
        self.client = client

    def request(self, feature_id: str, request: str, repository: Path) -> FeaturePlan:
        if not (repository / ".git").exists():
            raise ValueError("repository must be a Git checkout")
        self.store.create(feature_id, request, repository)
        record = self.store.get(feature_id)
        plan = self.client.plan(request, session_id=str(record["session_id"]))
        if not plan.acceptance_criteria or Path(plan.file_path).is_absolute():
            raise ValueError("plan must have criteria and a relative file path")
        self.store.update(
            feature_id, status="awaiting_approval", plan_json=plan.model_dump_json()
        )
        return plan

    def approve(self, feature_id: str) -> None:
        record = self.store.get(feature_id)
        if record["status"] != "awaiting_approval":
            raise ValueError("feature is not awaiting approval")
        self.store.update(feature_id, status="approved")

    def resume(self, feature_id: str, verifier: tuple[str, ...]) -> dict[str, object]:
        record = self.store.get(feature_id)
        if record["status"] not in {"approved", "revising"}:
            raise ValueError("feature is not ready to resume")
        repository = Path(str(record["repository"]))
        plan = FeaturePlan.model_validate_json(str(record["plan_json"]))
        verifier_output = ""
        for attempt in range(int(record["attempts"]) + 1, 3):
            self.client.implement(
                plan,
                repository=repository,
                session_id=str(record["session_id"]),
                verifier_output=verifier_output,
            )
            completed = subprocess.run(
                verifier,
                cwd=repository,
                text=True,
                capture_output=True,
                check=False,
            )
            self.store.update(feature_id, attempts=attempt)
            if completed.returncode == 0:
                sha = _commit(repository, plan.title)
                self.store.update(feature_id, status="completed", commit_sha=sha)
                return self.store.get(feature_id)
            verifier_output = (completed.stdout + completed.stderr).strip()
            self.store.update(feature_id, status="revising")
        self.store.update(feature_id, status="failed")
        return self.store.get(feature_id)


def _commit(repository: Path, title: str) -> str:
    subprocess.run(["git", "add", "--all"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"feat: {title}"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
