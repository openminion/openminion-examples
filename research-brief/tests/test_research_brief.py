from pathlib import Path

import pytest

from research_brief.app import Brief, ResearchBriefWorkflow, ResearchStore


class FakeResearch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.followups: list[tuple[str, str]] = []

    def research(self, topic: str, *, session_id: str, max_calls: int) -> Brief:
        self.calls.append((topic, session_id, max_calls))
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
        def research(self, topic: str, *, session_id: str, max_calls: int) -> Brief:
            return Brief(title=topic, summary="unsupported", citations=[])

    workflow = ResearchBriefWorkflow(
        ResearchStore(tmp_path / "app.db"), NoSources(), tmp_path / "artifacts"
    )
    with pytest.raises(ValueError, match="source URLs"):
        workflow.run("brief-1", "No sources")

    assert ResearchStore(tmp_path / "app.db").get("brief-1").status == "failed"
