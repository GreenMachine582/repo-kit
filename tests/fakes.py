"""In-memory stand-ins for GitHub, behind the `repo_kit.github.GitHub` protocol."""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any

from repo_kit import git
from repo_kit.credentials import EMPTY_FILE, SopsError
from repo_kit.github import BaseStatus, PullRequest, UpdateResult, pick_pr

PEM = "-----BEGIN RSA PRIVATE KEY-----\nYWJj\n-----END RSA PRIVATE KEY-----\n"
RELEASE_PEM = "-----BEGIN RSA PRIVATE KEY-----\ncmVs\n-----END RSA PRIVATE KEY-----\n"
NEW_PEM = "-----BEGIN RSA PRIVATE KEY-----\nbmV3\n-----END RSA PRIVATE KEY-----\n"  # a rotated key


class FakeSops:
    """Encrypted files as plain dicts in memory, behind `repo_kit.credentials.Sops`."""

    def __init__(self) -> None:
        self.files: dict[Path, dict[str, Any]] = {}
        self.recipients: dict[Path, list[str]] = {}
        self.readable = True  # False: this machine's age key isn't a recipient
        self.installed: tuple[int, int] | None = (3, 10)
        self.writes: list[str] = []

    def version(self) -> tuple[int, int] | None:
        return self.installed

    def decrypt(self, path: Path) -> dict[str, Any]:
        if not self.readable or path not in self.files:
            raise SopsError(f"`sops decrypt {path}` failed:\nno matching age key")
        return copy.deepcopy(self.files[path])

    def set_value(self, path: Path, keys: tuple[str, ...], value: str) -> None:
        self.writes.append(f"set {'.'.join(keys)}")
        node = self.files[path]
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    def create(self, path: Path, recipient: str) -> None:
        self.writes.append(f"create {path.name}")
        path.write_text("{}", encoding="utf-8")  # stands in for the ciphertext
        self.files[path] = copy.deepcopy(EMPTY_FILE)
        self.recipients[path] = [recipient]

    def add_recipient(self, path: Path, recipient: str) -> None:
        self.writes.append(f"add {recipient}")
        self.recipients.setdefault(path, []).append(recipient)


def stored_credentials(
    tmp_path: Path, pr_client: str = "Iv23abc", release_client: str = "Iv23rel"
) -> tuple[Path, FakeSops]:
    """A credentials file holding both Apps, and the fake sops that reads it."""
    path = tmp_path / "secrets.sops.json"
    path.write_text("{}", encoding="utf-8")
    sops = FakeSops()
    sops.files[path] = {
        "pr_bot": {"client_id": pr_client, "private_key": PEM},
        "release_bot": {"client_id": release_client, "private_key": RELEASE_PEM},
    }
    return path, sops


class FakeRepoSetup:
    """Repo labels, variables and secrets in memory, behind `repo_kit.github.RepoSetup`."""

    def __init__(self) -> None:
        self.label_set: dict[str, tuple[str, str]] = {}
        self.vars: dict[str, str] = {}
        self.secrets: dict[str, str] = {}
        self.ruleset_list: list[dict[str, Any]] = []
        self.settings: dict[str, Any] = {}
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

    def create_ruleset(self, payload: dict[str, Any]) -> None:
        self.writes.append(f"ruleset create {payload['name']}")
        new_id = max((r.get("id", 0) for r in self.ruleset_list), default=0) + 1
        self.ruleset_list.append({**copy.deepcopy(payload), "id": new_id})

    def update_ruleset(self, ruleset_id: int, payload: dict[str, Any]) -> None:
        self.writes.append(f"ruleset update {payload['name']}")
        [index] = [i for i, r in enumerate(self.ruleset_list) if r.get("id") == ruleset_id]
        self.ruleset_list[index] = {**copy.deepcopy(payload), "id": ruleset_id}

    def repo_settings(self) -> dict[str, Any]:
        return dict(self.settings)

    def update_repo_settings(self, values: dict[str, Any]) -> None:
        self.writes.append(f"settings {','.join(values)}")
        self.settings.update(values)


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
        head_sha: str = "",
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
            head_sha=head_sha,
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

    def prs_for_branch(self, branch: str) -> list[PullRequest]:
        return [pr for pr in self.prs if pr.head_ref == branch]

    def pr(self, number: int) -> PullRequest | None:
        return next((pr for pr in self.prs if pr.number == number), None)

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

    def upsert_comment(self, number: int, marker: str, body: str) -> bool:
        text = f"{marker}\n{body}"
        comments = self.comments.setdefault(number, [])
        for index, existing in enumerate(comments):
            if marker in existing:
                comments[index] = text
                return existing != text
        comments.append(text)
        return True

    def read_file(self, path: str) -> str | None:
        return self.files.get(path)
