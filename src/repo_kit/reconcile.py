"""`rk update-prs`: reconcile every open PR against its live base, parents before children.

Merge only, never force-push: GitHub does the merge through update-branch. Every run looks at every
open PR, so a dropped or failed run is harmless. See docs/pr-updates.md.
"""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from repo_kit import git
from repo_kit.config import (
    REPO_CONFIG_NAME,
    RepoConfig,
    parse_repo_config_text,
)
from repo_kit.errors import RkError
from repo_kit.github import BaseStatus, GhCliGitHub, GitHub, GitHubError, PullRequest

COMMENT_MARKER = "<!-- repo-kit:update-prs -->"
RELEASE_PLEASE_PREFIX = "release-please--"

# GitHub works out `mergeable` in the background, and a run triggered by a base push often asks
# before it has. There's no scheduled run to catch a PR left behind, so wait briefly.
_STATUS_ATTEMPTS = 4
_STATUS_DELAY_SECONDS = 3


class Action(StrEnum):
    SKIPPED = "skipped"
    UP_TO_DATE = "up to date"
    UPDATED = "updated"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    FAILED = "failed"


@dataclass(frozen=True)
class Row:
    number: int
    head: str
    base: str
    action: Action
    detail: str = ""


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(row.action is Action.FAILED for row in self.rows)


def skip_reason(pr: PullRequest, config: RepoConfig) -> str | None:
    """Why `pr` must be left alone, or None if it's eligible."""
    if pr.state != "open":
        return "no longer open"
    if pr.draft:
        return "draft"
    if pr.from_fork:
        return "from a fork"
    for label in config.update_prs.skip_labels:
        if label in pr.labels:
            return f"labelled {label}"
    if pr.head_ref.startswith(RELEASE_PLEASE_PREFIX):
        return "release-please branch"
    if pr.head_ref in config.long_lived_branches:
        return f"head is the long-lived branch {pr.head_ref}"
    return None


def parents_first(prs: list[PullRequest]) -> list[PullRequest]:
    """Order `prs` so a PR comes after the PR whose head is its base. Stable by number."""
    by_head = {pr.head_ref: pr for pr in prs}
    ordered: list[PullRequest] = []
    seen: set[int] = set()

    def visit(pr: PullRequest) -> None:
        if pr.number in seen:  # marked before recursing, so a cycle ends here
            return
        seen.add(pr.number)
        parent = by_head.get(pr.base_ref)
        if parent is not None:
            visit(parent)
        ordered.append(pr)

    for pr in sorted(prs, key=lambda pr: pr.number):
        visit(pr)
    return ordered


def _status(github: GitHub, pr: PullRequest, sleep: Callable[[float], None]) -> BaseStatus:
    status = github.base_status(pr)
    for _ in range(_STATUS_ATTEMPTS - 1):
        if status is not BaseStatus.UNKNOWN:
            break
        sleep(_STATUS_DELAY_SECONDS)
        status = github.base_status(pr)
    return status


def _clear_conflict_label(
    github: GitHub, pr: PullRequest, label: str, report: Report, dry_run: bool
) -> None:
    if label in pr.labels and not dry_run:
        github.remove_label(pr.number, label)
        report.changed.append(f"removed {label} from PR #{pr.number}")


def _flag_conflict(
    github: GitHub, pr: PullRequest, label: str, report: Report, dry_run: bool
) -> None:
    if dry_run:
        return
    if label not in pr.labels:
        github.add_label(pr.number, label)
        report.changed.append(f"added {label} to PR #{pr.number}")
    github.upsert_comment(
        pr.number,
        COMMENT_MARKER,
        f"This PR conflicts with `{pr.base_ref}`, so it can't be updated automatically. "
        f"Run `rk wt sync {pr.head_ref}`, resolve the conflicts and commit the merge.",
    )
    report.changed.append(f"flagged the conflict on PR #{pr.number}")


def _reconcile_one(
    github: GitHub,
    config: RepoConfig,
    listed: PullRequest,
    report: Report,
    dry_run: bool,
    sleep: Callable[[float], None],
) -> Row:
    # Read it again: labels, draft state and base may have changed since the list.
    fresh = github.pr_for_branch(listed.head_ref)
    if fresh is None:
        return Row(listed.number, listed.head_ref, listed.base_ref, Action.SKIPPED, "not found")
    reason = skip_reason(fresh, config)
    if reason:
        return Row(listed.number, listed.head_ref, fresh.base_ref, Action.SKIPPED, reason)

    # The listed SHA is what the status check and the update are guarded by.
    pr = dataclasses.replace(fresh, head_sha=listed.head_sha)
    label = config.update_prs.conflict_label
    status = _status(github, pr, sleep)

    def row(action: Action, detail: str = "") -> Row:
        return Row(pr.number, pr.head_ref, pr.base_ref, action, detail)

    if status is BaseStatus.CURRENT:
        _clear_conflict_label(github, pr, label, report, dry_run)
        return row(Action.UP_TO_DATE)
    if status is BaseStatus.UNKNOWN:
        return row(Action.UNKNOWN, "GitHub hasn't worked out mergeability yet")
    if status is BaseStatus.CONFLICTING:
        _flag_conflict(github, pr, label, report, dry_run)
        return row(Action.CONFLICT, "dry run: would flag it" if dry_run else "flagged")
    if dry_run:
        return row(Action.UPDATED, "dry run: would merge the base in")
    result = github.update_branch(pr.number, pr.head_sha)
    if not result.ok:
        return row(Action.REJECTED, result.detail)
    report.changed.append(f"asked GitHub to merge {pr.base_ref} into {pr.head_ref} (#{pr.number})")
    _clear_conflict_label(github, pr, label, report, dry_run)
    return row(Action.UPDATED)


def reconcile(
    github: GitHub,
    config: RepoConfig,
    *,
    dry_run: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Bring every eligible open PR up to date with its base, and record what happened to each."""
    report = Report()
    eligible: list[PullRequest] = []
    for pr in github.list_open_prs():
        reason = skip_reason(pr, config)
        if reason:
            report.rows.append(Row(pr.number, pr.head_ref, pr.base_ref, Action.SKIPPED, reason))
        else:
            eligible.append(pr)
    for pr in parents_first(eligible):
        try:
            row = _reconcile_one(github, config, pr, report, dry_run, sleep)
        except GitHubError as exc:
            # One PR's failure mustn't stop the others; the command still exits non-zero.
            row = Row(pr.number, pr.head_ref, pr.base_ref, Action.FAILED, str(exc))
        report.rows.append(row)
    return report


def render_summary(report: Report) -> str:
    """The report as a Markdown table, one row per PR."""

    def cell(text: str) -> str:
        return " ".join(text.split()).replace("|", "\\|")

    lines = ["| PR | Branch | Base | Action | Detail |", "|---|---|---|---|---|"]
    for row in sorted(report.rows, key=lambda row: row.number):
        cells = [f"#{row.number}", cell(row.head), cell(row.base), row.action, cell(row.detail)]
        lines.append(f"| {' | '.join(cells)} |")
    return "\n".join(lines) + "\n"


def _resolve_repo(cwd: Path) -> tuple[str, str]:
    """The repo to work on: `GITHUB_REPOSITORY` in Actions, otherwise `origin`."""
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        owner, _, repo = env.partition("/")
        if not owner or not repo:
            raise RkError(f"GITHUB_REPOSITORY must be owner/name, got {env!r}")
        return owner, repo
    url = git.remote_url(cwd)
    if not git.is_github_url(url):
        raise RkError(f"origin isn't on GitHub ({url}), so there are no PRs to update")
    return git.parse_owner_repo(url)


def load_config(github: GitHub) -> RepoConfig:
    """Read the config from the default branch, as data: a PR's own copy is never used."""
    text = github.read_file(REPO_CONFIG_NAME)
    return RepoConfig() if text is None else parse_repo_config_text(text, Path(REPO_CONFIG_NAME))


def run(cwd: Path, *, dry_run: bool = False, github: GitHub | None = None) -> Report:
    """The command: reconcile, then print the table and write the Actions job summary."""
    if github is None:
        github = GhCliGitHub(*_resolve_repo(cwd))
    config = load_config(github)
    if not config.update_prs.enabled:
        print("update-prs is disabled in .repo-kit.toml ([update_prs].enabled)")
        return Report()
    report = reconcile(github, config, dry_run=dry_run)
    summary = render_summary(report)
    print(summary, end="")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as f:
            f.write(summary)
    return report
