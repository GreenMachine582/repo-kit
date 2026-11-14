"""The GitHub interface: PRs, labels, comments, rulesets and repo settings, through `gh api`.

Everything that talks to GitHub goes through the `GitHub` protocol, so scenario tests swap in a
fake PR store (`tests/fakes.py`). `wt sync` and `wt done` read one PR and clear a label;
`update-prs` lists every open PR and updates, labels and comments on them.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

from repo_kit.errors import RkError

_PAGE_SIZE = 100


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
    head_sha: str = ""
    from_fork: bool = False

    @property
    def finished(self) -> bool:
        """Merged or closed: the branch's work is done with."""
        return self.merged or self.state == "closed"


class BaseStatus(StrEnum):
    """How a PR's head stands against its live base."""

    CURRENT = "current"  # already has every base commit
    BEHIND = "behind"  # missing base commits, and they merge cleanly
    CONFLICTING = "conflicting"  # missing base commits, and they conflict
    UNKNOWN = "unknown"  # GitHub hasn't worked out mergeability yet


@dataclass(frozen=True)
class UpdateResult:
    """Whether GitHub took an update-branch request. A refusal is a result, not an error."""

    ok: bool
    detail: str = ""


class GitHub(Protocol):
    def pr_for_branch(self, branch: str) -> PullRequest | None:
        """The PR whose head is `branch` in this repo: the open one, else the most recent."""
        ...

    def remove_label(self, number: int, label: str) -> None:
        """Remove `label` from PR `number`; a label that isn't there is not an error."""
        ...

    def list_open_prs(self) -> list[PullRequest]:
        """Every open PR in this repo, across all pages."""
        ...

    def base_status(self, pr: PullRequest) -> BaseStatus:
        """How `pr`'s head stands against its live base."""
        ...

    def update_branch(self, number: int, expected_head_sha: str) -> UpdateResult:
        """Merge PR `number`'s base into its head, only if the head is still `expected_head_sha`."""
        ...

    def add_label(self, number: int, label: str) -> None:
        """Add `label` to PR `number`."""
        ...

    def upsert_comment(self, number: int, marker: str, body: str) -> None:
        """Keep one comment on PR `number`, found again by the hidden `marker`."""
        ...

    def read_file(self, path: str) -> str | None:
        """The text of `path` on the default branch, or None if it isn't there."""
        ...

    def retarget(self, number: int, base: str) -> None:
        """Change PR `number`'s base branch to `base`."""
        ...


def pick_pr(prs: list[PullRequest]) -> PullRequest | None:
    """Prefer an open PR; otherwise the most recent (highest number)."""
    open_prs = [pr for pr in prs if pr.state == "open"]
    candidates = open_prs or prs
    return max(candidates, key=lambda pr: pr.number, default=None)


def pr_from_api(data: dict[str, Any]) -> PullRequest:
    # A deleted fork leaves `head.repo` null.
    head_repo = data["head"].get("repo")
    return PullRequest(
        number=data["number"],
        state=data["state"],
        merged=bool(data.get("merged_at")),
        draft=bool(data.get("draft")),
        base_ref=data["base"]["ref"],
        head_ref=data["head"]["ref"],
        labels=tuple(label["name"] for label in data.get("labels", [])),
        head_sha=data["head"]["sha"],
        from_fork=head_repo is None or head_repo["full_name"] != data["base"]["repo"]["full_name"],
    )


class NoGitHub:
    """For an origin that isn't on GitHub (a local or other remote): there are no PRs."""

    def pr_for_branch(self, branch: str) -> PullRequest | None:
        return None

    def remove_label(self, number: int, label: str) -> None:
        return None

    def list_open_prs(self) -> list[PullRequest]:
        return []

    def base_status(self, pr: PullRequest) -> BaseStatus:
        raise GitHubError("origin isn't on GitHub, so there are no PRs to check")

    def update_branch(self, number: int, expected_head_sha: str) -> UpdateResult:
        raise GitHubError("origin isn't on GitHub, so there are no PRs to update")

    def add_label(self, number: int, label: str) -> None:
        return None

    def upsert_comment(self, number: int, marker: str, body: str) -> None:
        return None

    def read_file(self, path: str) -> str | None:
        return None

    def retarget(self, number: int, base: str) -> None:
        raise GitHubError("origin isn't on GitHub, so there are no PRs to retarget")


class RepoSetup(Protocol):
    """The repo-level settings `rk configure` reads and writes."""

    def labels(self) -> list[str]:
        """The names of every label in the repo."""
        ...

    def create_label(self, name: str, color: str, description: str) -> None: ...

    def variables(self) -> dict[str, str]:
        """Every Actions variable in the repo, by name."""
        ...

    def set_variable(self, name: str, value: str) -> None: ...

    def secret_names(self) -> list[str]:
        """The names of the repo's Actions secrets. Their values can't be read back."""
        ...

    def set_secret(self, name: str, value: str) -> None:
        """Store an Actions secret. The value goes to `gh` on stdin, never as an argument."""
        ...


class GhCliGitHub:
    """`GitHub` and `RepoSetup` backed by the `gh` CLI, which handles auth and hosts."""

    def __init__(self, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo

    @property
    def _repo(self) -> str:
        return f"repos/{self.owner}/{self.repo}"

    def _gh(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        """Run `gh <args>`. Anything secret goes in `stdin`, never in `args`."""
        try:
            return subprocess.run(
                ["gh", *args],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise GitHubError(
                "gh is not on PATH: install the GitHub CLI and `gh auth login`"
            ) from exc

    def _gh_ok(self, *args: str, stdin: str | None = None) -> str:
        proc = self._gh(*args, stdin=stdin)
        if proc.returncode != 0:
            raise GitHubError(f"`gh {' '.join(args)}` failed:\n{proc.stderr.strip()}")
        return proc.stdout

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._gh("api", *args)

    def _api(self, *args: str, allow_404: bool = False) -> Any:
        proc = self._run(*args)
        if proc.returncode != 0:
            if allow_404 and "HTTP 404" in proc.stderr:
                return None
            raise GitHubError(f"`gh api {' '.join(args)}` failed:\n{proc.stderr.strip()}")
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def _paged(self, path: str) -> list[Any]:
        """Every item of a list endpoint."""
        separator = "&" if "?" in path else "?"
        items: list[Any] = []
        page = 1
        while True:
            batch = self._api(f"{path}{separator}per_page={_PAGE_SIZE}&page={page}") or []
            items.extend(batch)
            if len(batch) < _PAGE_SIZE:
                return items
            page += 1

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

    def list_open_prs(self) -> list[PullRequest]:
        return [pr_from_api(item) for item in self._paged(f"{self._repo}/pulls?state=open")]

    def base_status(self, pr: PullRequest) -> BaseStatus:
        base = quote(pr.base_ref, safe="/")
        head = quote(pr.head_ref, safe="/")
        compare = self._api(f"{self._repo}/compare/{base}...{head}?per_page=1")
        if compare["behind_by"] == 0:
            return BaseStatus.CURRENT
        mergeable = self._api(f"{self._repo}/pulls/{pr.number}")["mergeable"]
        if mergeable is None:
            return BaseStatus.UNKNOWN
        return BaseStatus.BEHIND if mergeable else BaseStatus.CONFLICTING

    def update_branch(self, number: int, expected_head_sha: str) -> UpdateResult:
        # 422 is GitHub refusing this update, such as the head having moved. Anything else, such as
        # a missing App permission, is a failure that must be seen.
        args = (
            "-X",
            "PUT",
            f"{self._repo}/pulls/{number}/update-branch",
            "-f",
            f"expected_head_sha={expected_head_sha}",
        )
        proc = self._run(*args)
        if proc.returncode == 0:
            return UpdateResult(ok=True)
        if "HTTP 422" in proc.stderr:
            return UpdateResult(ok=False, detail=proc.stderr.strip())
        raise GitHubError(f"`gh api {' '.join(args)}` failed:\n{proc.stderr.strip()}")

    def add_label(self, number: int, label: str) -> None:
        self._api("-X", "POST", f"{self._repo}/issues/{number}/labels", "-f", f"labels[]={label}")

    def upsert_comment(self, number: int, marker: str, body: str) -> None:
        text = f"{marker}\n{body}"
        path = f"{self._repo}/issues/{number}/comments"
        existing = next((c for c in self._paged(path) if marker in c["body"]), None)
        if existing is None:
            self._api("-X", "POST", path, "-f", f"body={text}")
        elif existing["body"] != text:
            comment = f"{self._repo}/issues/comments/{existing['id']}"
            self._api("-X", "PATCH", comment, "-f", f"body={text}")

    def read_file(self, path: str) -> str | None:
        data = self._api(f"{self._repo}/contents/{quote(path)}", allow_404=True)
        if data is None:
            return None
        return base64.b64decode(data["content"]).decode("utf-8")

    def retarget(self, number: int, base: str) -> None:
        self._api("-X", "PATCH", f"{self._repo}/pulls/{number}", "-f", f"base={base}")

    # --- RepoSetup ---

    @property
    def _full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def labels(self) -> list[str]:
        return [label["name"] for label in self._paged(f"{self._repo}/labels")]

    def create_label(self, name: str, color: str, description: str) -> None:
        self._api(
            "-X",
            "POST",
            f"{self._repo}/labels",
            "-f",
            f"name={name}",
            "-f",
            f"color={color}",
            "-f",
            f"description={description}",
        )

    def variables(self) -> dict[str, str]:
        out = self._gh_ok("variable", "list", "--repo", self._full_name, "--json", "name,value")
        return {item["name"]: item["value"] for item in json.loads(out or "[]")}

    def set_variable(self, name: str, value: str) -> None:
        self._gh_ok("variable", "set", name, "--repo", self._full_name, "--body", value)

    def secret_names(self) -> list[str]:
        out = self._gh_ok("secret", "list", "--repo", self._full_name, "--json", "name")
        return [item["name"] for item in json.loads(out or "[]")]

    def set_secret(self, name: str, value: str) -> None:
        # Without --body, gh reads the value from stdin: it never appears in a process listing.
        self._gh_ok("secret", "set", name, "--repo", self._full_name, stdin=value)
