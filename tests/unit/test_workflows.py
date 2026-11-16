"""Every workflow runs rk with `python -P`, so a PR's own `repo_kit/` can never stand in for rk."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted(
    [*(ROOT / ".github" / "workflows").glob("*.yml"), *(ROOT / "templates").rglob("*.yml")]
)
RUNS_RK = re.compile(r"\bpython3?\b[^\n]*-m\s+repo_kit\b")


def test_there_are_workflows_that_run_rk():
    assert any(RUNS_RK.search(p.read_text(encoding="utf-8")) for p in WORKFLOWS)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_rk_is_run_with_safe_path(path):
    for line in RUNS_RK.findall(path.read_text(encoding="utf-8")):
        assert re.search(r"\bpython3?\s+(-\w+\s+)*-P\b", line), f"missing -P: {line.strip()}"


# --- App keys only inside branch-locked environments (docs/github-app.md#where-the-keys-live) ---

KEY_ENVIRONMENTS = {
    "PR_BOT_PRIVATE_KEY": "gth-pr-bot",
    "RELEASE_BOT_PRIVATE_KEY": "gth-release-bot",
}


def jobs(workflow: str) -> dict[str, str]:
    """Each job's block, by id: the keys two spaces in under `jobs:` (no YAML parser here)."""
    body = workflow.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z][a-z0-9_-]*):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_some_job_uses_each_key():
    used = {
        key
        for path in WORKFLOWS
        for key in KEY_ENVIRONMENTS
        if f"private-key: ${{{{ secrets.{key} }}}}" in path.read_text(encoding="utf-8")
    }
    assert used == set(KEY_ENVIRONMENTS)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_a_job_that_makes_an_app_token_sets_the_keys_environment(path):
    for job, block in jobs(path.read_text(encoding="utf-8")).items():
        for key, environment in KEY_ENVIRONMENTS.items():
            if f"private-key: ${{{{ secrets.{key} }}}}" in block:
                assert f"\n    environment: {environment}\n" in block, f"{job} reads {key}"


def test_the_trigger_holds_nothing():
    text = (ROOT / "templates/common/.github/workflows/update-prs-trigger.yml").read_text(
        encoding="utf-8"
    )
    assert "secrets." not in text
    assert "permissions: {}" in text
    assert "name: update-prs trigger\n" in text


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "templates/common/.github/workflows/update-prs.yml",
        ROOT / ".github/workflows/keep-prs-current.yml",
    ],
    ids=lambda p: p.name,
)
def test_the_update_prs_callers_run_only_from_the_default_branch(path):
    text = path.read_text(encoding="utf-8")
    triggers = text.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    assert re.findall(r"^  ([a-z_]+):", triggers, flags=re.MULTILINE) == [
        "workflow_run",
        "workflow_dispatch",
    ]
    assert "workflows: [update-prs trigger]" in triggers
