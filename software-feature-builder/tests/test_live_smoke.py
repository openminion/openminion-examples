from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from software_feature_builder.app import FeatureBuilder, FeatureStore, OpenMinionCoding


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.live
def test_live_revision_reconstruction_and_git_truth(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = os.getenv("OPENMINION_LIVE_CONFIG")
    if not config_path:
        pytest.skip("OPENMINION_LIVE_CONFIG is not set")

    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "OpenMinion Example")
    _git(repository, "config", "user.email", "example@openminion.local")
    (repository / "unrelated.txt").write_text("preserve\n", encoding="utf-8")
    (repository / "verify.py").write_text(
        "from pathlib import Path\n"
        "marker = Path('.git/oaap-first-verifier')\n"
        "if not marker.exists():\n"
        "    marker.write_text('failed once')\n"
        "    raise SystemExit('seeded verifier failure')\n"
        "namespace = {}\n"
        "exec(Path('hello.py').read_text(), namespace)\n"
        "assert namespace['answer']() == 42\n",
        encoding="utf-8",
    )
    _git(repository, "add", "unrelated.txt", "verify.py")
    _git(repository, "commit", "-m", "chore: seed disposable repository")

    monkeypatch.setenv("OPENMINION_WORKSPACE_ROOT", str(repository))
    store_path = tmp_path / "features.db"
    first_client = OpenMinionCoding(config_path)
    try:
        builder = FeatureBuilder(FeatureStore(store_path), first_client)
        plan = builder.request(
            "feature-1",
            "Create hello.py with a function answer() that returns the integer 42.",
            repository,
        )
        assert plan.file_path == "hello.py"
        builder.approve("feature-1")
    finally:
        first_client.close()

    reconstructed_client = OpenMinionCoding(config_path)
    try:
        result = FeatureBuilder(FeatureStore(store_path), reconstructed_client).resume(
            "feature-1", (sys.executable, "verify.py")
        )
    finally:
        reconstructed_client.close()

    assert result["status"] == "completed"
    assert result["attempts"] == 2
    assert result["commit_sha"] == _git(repository, "rev-parse", "HEAD")
    assert _git(repository, "status", "--short") == ""
    assert (repository / "unrelated.txt").read_text(encoding="utf-8") == "preserve\n"
