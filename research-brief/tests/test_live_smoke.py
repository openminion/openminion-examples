import os
from pathlib import Path

import pytest

from research_brief.app import OpenMinionResearch, ResearchBriefWorkflow, ResearchStore


@pytest.mark.live
def test_live_research_brief(tmp_path: Path) -> None:
    config_path = os.getenv("OPENMINION_LIVE_CONFIG")
    if not config_path:
        pytest.skip("OPENMINION_LIVE_CONFIG is not set")
    client = OpenMinionResearch(config_path)
    try:
        request = ResearchBriefWorkflow(
            ResearchStore(tmp_path / "app.db"), client, tmp_path / "artifacts"
        ).run("live-brief", "OpenMinion public application SDK")
    finally:
        client.close()
    assert Path(request.artifact_path).exists()
