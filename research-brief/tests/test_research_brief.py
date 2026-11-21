import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_brief.app import (
    READ_ONLY_RESEARCH_TOOLS,
    TURN_TIMEOUT_SECONDS,
    Brief,
    OpenMinionResearch,
    ResearchBriefWorkflow,
    ResearchStore,
)


class FakeResearch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.followups: list[tuple[str, str]] = []

    def research(
        self, topic: str, *, session_id: str, max_calls_instruction: int
    ) -> Brief:
        self.calls.append((topic, session_id, max_calls_instruction))
        return Brief(
            title=topic,
            summary="verified summary",
            citations=["https://example.com/source"],
        )

    def follow_up(self, question: str, *, session_id: str) -> str:
        self.followups.append((question, session_id))
        return "follow-up answer"


def test_cited_artifact_and_post_restart_follow_up(tmp_path: Path) -> None:
    client = FakeResearch()
    db = tmp_path / "app.db"
    workflow = ResearchBriefWorkflow(ResearchStore(db), client, tmp_path / "artifacts")
    request = workflow.run("brief-1", "Packaging")

    artifact = Path(request.artifact_path)
    assert artifact.exists()
    assert Brief.model_validate_json(artifact.read_text()).citations
    assert client.calls[0][2] == 8

    restarted = ResearchBriefWorkflow(ResearchStore(db), client, tmp_path / "artifacts")
    answer = restarted.follow_up("brief-1", "What is most important?")
    assert answer == "follow-up answer"
    assert client.followups[-1][1] == request.session_id


def test_missing_citations_fail_truthfully(tmp_path: Path) -> None:
    class NoSources(FakeResearch):
        def research(
            self, topic: str, *, session_id: str, max_calls_instruction: int
        ) -> Brief:
            return Brief(title=topic, summary="unsupported", citations=[])

    workflow = ResearchBriefWorkflow(
        ResearchStore(tmp_path / "app.db"), NoSources(), tmp_path / "artifacts"
    )
    with pytest.raises(ValueError, match="source URLs"):
        workflow.run("brief-1", "No sources")

    assert ResearchStore(tmp_path / "app.db").get("brief-1").status == "failed"


def test_openminion_client_exposes_only_read_only_research_tools(monkeypatch) -> None:
    agent_calls: list[dict[str, object]] = []

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            agent_calls.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def run(self, prompt: str, *, timeout_seconds: float):
            assert timeout_seconds == TURN_TIMEOUT_SECONDS
            if "exact JSON" in prompt:
                return SimpleNamespace(
                    output=Brief(
                        title="Tools",
                        summary="Read only",
                        citations=["https://example.com/source"],
                    )
                )
            return SimpleNamespace(text="follow-up")

    monkeypatch.setattr("research_brief.app.Agent", FakeAgent)
    client = OpenMinionResearch.__new__(OpenMinionResearch)
    client.runtime = object()
    client.agent_id = None

    client.research("Tools", session_id="session", max_calls_instruction=2)
    client.follow_up("More?", session_id="session")

    assert len(agent_calls) == 2
    assert all(call["tools"] == list(READ_ONLY_RESEARCH_TOOLS) for call in agent_calls)


def test_later_turn_uses_persisted_session_after_process_restart(
    tmp_path: Path,
) -> None:
    source_root = Path(__file__).parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(source_root)}
    database = tmp_path / "process-restart.db"
    artifacts = tmp_path / "artifacts"
    create_script = """
import sys
from research_brief.app import Brief, ResearchBriefWorkflow, ResearchStore

class Client:
    def research(self, topic, *, session_id, max_calls_instruction):
        return Brief(title=topic, summary="summary", citations=["https://example.com"])
    def follow_up(self, question, *, session_id):
        return session_id

workflow = ResearchBriefWorkflow(ResearchStore(sys.argv[1]), Client(), sys.argv[2])
request = workflow.run("brief", "Restart")
print(request.session_id)
"""
    config = tmp_path / "agents.json"
    config.write_text(
        json.dumps(
            {
                "default_agent": "openminion",
                "agents": {
                    "openminion": {
                        "name": "openminion",
                        "provider": "echo",
                        "default_channel": "console",
                    }
                },
                "storage": {"path": str(tmp_path / "runtime.db")},
            }
        ),
        encoding="utf-8",
    )
    follow_up_script = """
import json
import sys
from research_brief.app import OpenMinionResearch, ResearchBriefWorkflow, ResearchStore

client = OpenMinionResearch(sys.argv[3])
try:
    workflow = ResearchBriefWorkflow(ResearchStore(sys.argv[1]), client, sys.argv[2])
    request = workflow.store.get("brief")
    answer = workflow.follow_up("brief", "More?")
    print(json.dumps({"session_id": request.session_id, "answer": answer}))
finally:
    client.close()
"""
    created = subprocess.run(
        [sys.executable, "-c", create_script, str(database), str(artifacts)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    resumed = subprocess.run(
        [
            sys.executable,
            "-c",
            follow_up_script,
            str(database),
            str(artifacts),
            str(config),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()

    resumed_payload = json.loads(resumed)
    assert resumed_payload["session_id"] == created
    assert "More?" in resumed_payload["answer"]
