"""In-memory stand-ins for GitHub, behind the `repo_kit.github.GitHub` protocol."""

from __future__ import annotations

import dataclasses

from repo_kit.github import PullRequest, pick_pr


class FakeGitHub:
    """A PR store: tests add PRs, rk reads them and removes labels."""

    def __init__(self) -> None:
        self.prs: list[PullRequest] = []
        self.removed_labels: list[tuple[int, str]] = []

    def add_pr(
        self,
        head: str,
        base: str = "dev",
        state: str = "open",
        merged: bool = False,
        draft: bool = False,
        labels: tuple[str, ...] = (),
    ) -> PullRequest:
        pr = PullRequest(
            number=len(self.prs) + 1,
            state="closed" if merged else state,
            merged=merged,
            draft=draft,
            base_ref=base,
            head_ref=head,
            labels=tuple(labels),
        )
        self.prs.append(pr)
        return pr

    def get(self, number: int) -> PullRequest:
        return next(pr for pr in self.prs if pr.number == number)

    # --- the GitHub protocol ---

    def pr_for_branch(self, branch: str) -> PullRequest | None:
        return pick_pr([pr for pr in self.prs if pr.head_ref == branch])

    def remove_label(self, number: int, label: str) -> None:
        self.removed_labels.append((number, label))
        self.prs = [
            dataclasses.replace(pr, labels=tuple(lb for lb in pr.labels if lb != label))
            if pr.number == number
            else pr
            for pr in self.prs
        ]
