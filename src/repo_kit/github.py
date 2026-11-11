"""The GitHub interface: PRs, labels, comments, rulesets and repo settings, through `gh api`.

Everything that talks to GitHub goes through the `GitHub` protocol, so scenario tests swap in a
fake PR store (`tests/fakes.py`). Phase 1A needs only what `wt sync` and `wt done` use; Phase 1B
extends the protocol for `update-prs`.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

from repo_kit.errors import RkError


class GitHubError(RkError):
    """A GitHub API call failed."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    state: str  # "open" or "closed"
    merged: bool
    draft: bool
    base_ref: str
    head_ref: str
    labels: tuple[str, ...] = field(default=())

    @property
    def finished(self) -> bool:
        """Merged or closed: the branch's work is done with."""
        return self.merged or self.state == "closed"


class GitHub(Protocol):
    def pr_for_branch(self, branch: str) -> PullRequest | None:
        """The PR whose head is `branch` in this repo: the open one, else the most recent."""
        ...

    def remove_label(self, number: int, label: str) -> None:
        """Remove `label` from PR `number`; a label that isn't there is not an error."""
        ...


def pick_pr(prs: list[PullRequest]) -> PullRequest | None:
    """Prefer an open PR; otherwise the most recent (highest number)."""
    open_prs = [pr for pr in prs if pr.state == "open"]
    candidates = open_prs or prs
    return max(candidates, key=lambda pr: pr.number, default=None)


def pr_from_api(data: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=data["number"],
        state=data["state"],
        merged=bool(data.get("merged_at")),
        draft=bool(data.get("draft")),
        base_ref=data["base"]["ref"],
        head_ref=data["head"]["ref"],
        labels=tuple(label["name"] for label in data.get("labels", [])),
    )


class NoGitHub:
    """For an origin that isn't on GitHub (a local or other remote): there are no PRs."""

    def pr_for_branch(self, branch: str) -> PullRequest | None:
        return None

    def remove_label(self, number: int, label: str) -> None:
        return None


class GhCliGitHub:
    """`GitHub` backed by the `gh` CLI, which handles auth and hosts."""

    def __init__(self, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo

    def _api(self, *args: str, allow_404: bool = False) -> Any:
        try:
            proc = subprocess.run(
                ["gh", "api", *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise GitHubError(
                "gh is not on PATH: install the GitHub CLI and `gh auth login`"
            ) from exc
        if proc.returncode != 0:
            if allow_404 and "HTTP 404" in proc.stderr:
                return None
            raise GitHubError(f"`gh api {' '.join(args)}` failed:\n{proc.stderr.strip()}")
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def pr_for_branch(self, branch: str) -> PullRequest | None:
        head = quote(f"{self.owner}:{branch}", safe="")
        # A head filter matches a handful of PRs at most, so one page is enough.
        data = self._api(f"repos/{self.owner}/{self.repo}/pulls?head={head}&state=all&per_page=100")
        return pick_pr([pr_from_api(item) for item in data or []])

    def remove_label(self, number: int, label: str) -> None:
        self._api(
            "-X",
            "DELETE",
            f"repos/{self.owner}/{self.repo}/issues/{number}/labels/{quote(label, safe='')}",
            allow_404=True,
        )
