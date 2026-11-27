import subprocess
from pathlib import Path

from software_feature_builder.app import FeatureBuilder, FeaturePlan, FeatureStore


class FakeCoding:
    def __init__(self) -> None:
        self.implementations = 0

    def plan(self, request: str, *, session_id: str) -> FeaturePlan:
        return FeaturePlan(
            title="add feature marker",
            file_path="feature.txt",
            acceptance_criteria=["feature.txt contains feature ok"],
        )

    def implement(
        self,
        plan: FeaturePlan,
        *,
        repository: Path,
        session_id: str,
        verifier_output: str = "",
    ) -> None:
        self.implementations += 1
        content = "feature ok\n" if verifier_output else "not ready\n"
        (repository / plan.file_path).write_text(content, encoding="utf-8")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_plan_approval_reconstruction_revision_verification_and_git_truth(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "example@openminion.local")
    _git(repository, "config", "user.name", "OpenMinion Example")
    (repository / "README.md").write_text("# project\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "chore: initialize project")

    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep\n", encoding="utf-8")
    store_path = tmp_path / "features.db"
    client = FakeCoding()
    builder = FeatureBuilder(FeatureStore(store_path), client)
    plan = builder.request("feature-1", "Add a marker", repository)
    assert plan.acceptance_criteria
    builder.approve("feature-1")

    reconstructed = FeatureBuilder(FeatureStore(store_path), client)
    verifier = (
        "python3",
        "-c",
        "from pathlib import Path; "
        "assert Path('feature.txt').read_text() == 'feature ok\\n'",
    )
    result = reconstructed.resume("feature-1", verifier)

    assert result["status"] == "completed"
    assert result["attempts"] == 2
    assert client.implementations == 2
    assert result["commit_sha"] == _git(repository, "rev-parse", "HEAD")
    assert _git(repository, "status", "--porcelain") == ""
    assert unrelated.read_text() == "keep\n"
