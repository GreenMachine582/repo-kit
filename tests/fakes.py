"""In-memory stand-ins for GitHub, behind the `repo_kit.github.GitHub` protocol."""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any

from repo_kit import git
from repo_kit.github import BaseStatus, PullRequest, UpdateResult, pick_pr


class FakeRepoSetup:
    """Repo labels, variables and secrets in memory, behind `repo_kit.github.RepoSetup`."""

    def __init__(self) -> None:
        self.label_set: dict[str, tuple[str, str]] = {}
        self.vars: dict[str, str] = {}
        self.secrets: dict[str, str] = {}
        self.ruleset_list: list[dict[str, Any]] = []
        self.writes: list[str] = []

    def labels(self) -> list[str]:
        return list(self.label_set)

    def create_label(self, name: str, color: str, description: str) -> None:
        self.writes.append(f"label {name}")
        self.label_set[name] = (color, description)

    def variables(self) -> dict[str, str]:
        return dict(self.vars)

    def set_variable(self, name: str, value: str) -> None:
        self.writes.append(f"variable {name}")
        self.vars[name] = value

    def secret_names(self) -> list[str]:
        return list(self.secrets)

    def set_secret(self, name: str, value: str) -> None:
        self.writes.append(f"secret {name}")
        self.secrets[name] = value

    def rulesets(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.ruleset_list)


class FakeGitHub:
    """A PR store: tests add PRs, and rk reads, labels, comments on and updates them.

    With a bare `origin`, heads are read live and updates are real merges there. An update finishes
    at once, where GitHub answers 202 and does the work asynchronously.
    """

    def __init__(self, origin: Path | None = None) -> None:
        self.origin = origin
        self.prs: list[PullRequest] = []
        self.removed_labels: list[tuple[int, str]] = []
        self.added_labels: list[tuple[int, str]] = []
        self.comments: dict[int, list[str]] = {}
        self.files: dict[str, str] = {}

    def add_pr(
        self,
        head: str,
        base: str = "dev",
        state: str = "open",
        merged: bool = False,
        draft: bool = False,
        labels: tuple[str, ...] = (),
        from_fork: bool = False,
    ) -> PullRequest:
        pr = PullRequest(
            number=len(self.prs) + 1,
            state="closed" if merged else state,
            merged=merged,
            draft=draft,
            base_ref=base,
            head_ref=head,
            labels=tuple(labels),
            from_fork=from_fork,
        )
        self.prs.append(pr)
        return pr

    def get(self, number: int) -> PullRequest:
        return next(pr for pr in self.prs if pr.number == number)

    def retarget(self, number: int, base: str) -> None:
        self.prs = [
            dataclasses.replace(pr, base_ref=base) if pr.number == number else pr for pr in self.prs
        ]

    def _bare(self) -> Path:
        assert self.origin is not None, "this call needs FakeGitHub(origin)"
        return self.origin

    def _sha(self, branch: str) -> str:
        return git.out(["rev-parse", f"refs/heads/{branch}"], self._bare())

    def _merge_tree(self, head: str, base: str) -> str | None:
        """The tree of merging `base` into `head`, or None on a conflict."""
        proc = git.run(["merge-tree", "--write-tree", head, base], self._bare(), check=False)
        return proc.stdout.splitlines()[0] if proc.returncode == 0 else None

    def _set_labels(self, number: int, labels: tuple[str, ...]) -> None:
        self.prs = [
            dataclasses.replace(pr, labels=labels) if pr.number == number else pr for pr in self.prs
        ]

    # --- the GitHub protocol ---

    def pr_for_branch(self, branch: str) -> PullRequest | None:
        return pick_pr([pr for pr in self.prs if pr.head_ref == branch])

    def remove_label(self, number: int, label: str) -> None:
        self.removed_labels.append((number, label))
        self._set_labels(number, tuple(lb for lb in self.get(number).labels if lb != label))

    def list_open_prs(self) -> list[PullRequest]:
        return [
            dataclasses.replace(pr, head_sha=self._sha(pr.head_ref))
            for pr in self.prs
            if pr.state == "open"
        ]

    def base_status(self, pr: PullRequest) -> BaseStatus:
        head, base = self._sha(pr.head_ref), self._sha(pr.base_ref)
        ancestor = git.run(["merge-base", "--is-ancestor", base, head], self._bare(), check=False)
        if ancestor.returncode == 0:
            return BaseStatus.CURRENT
        return BaseStatus.BEHIND if self._merge_tree(head, base) else BaseStatus.CONFLICTING

    def update_branch(self, number: int, expected_head_sha: str) -> UpdateResult:
        pr = self.get(number)
        head, base = self._sha(pr.head_ref), self._sha(pr.base_ref)
        if head != expected_head_sha:
            return UpdateResult(ok=False, detail="the head moved (HTTP 422)")
        tree = self._merge_tree(head, base)
        if tree is None:
            return UpdateResult(ok=False, detail="merge conflict (HTTP 422)")
        message = f"Merge branch '{pr.base_ref}' into {pr.head_ref}"
        merge = git.out(["commit-tree", tree, "-p", head, "-p", base, "-m", message], self._bare())
        git.run(["update-ref", f"refs/heads/{pr.head_ref}", merge, head], self._bare())
        return UpdateResult(ok=True)

    def add_label(self, number: int, label: str) -> None:
        self.added_labels.append((number, label))
        labels = self.get(number).labels
        if label not in labels:
            self._set_labels(number, (*labels, label))

    def upsert_comment(self, number: int, marker: str, body: str) -> None:
        text = f"{marker}\n{body}"
        comments = self.comments.setdefault(number, [])
        for index, existing in enumerate(comments):
            if marker in existing:
                comments[index] = text
                return
        comments.append(text)

    def read_file(self, path: str) -> str | None:
        return self.files.get(path)
