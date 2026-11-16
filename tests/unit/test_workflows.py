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
