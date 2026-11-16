"""`rk branches prune`: list, or with `--apply` delete, branches whose work is done.

Replaces `prune-branches.sh`, with the same rules:
- local: a branch whose PR merged, with no open PR, and with no commits beyond what that PR merged;
- origin: a branch whose PR merged or was closed, with no open PR and no commits beyond that PR's
  head (stale `release-please--branches--*` branches included).
The profile's long-lived branches and every branch checked out in a worktree are never touched, and
a deletion is never forced on origin. See docs/rulesets.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from repo_kit import git
from repo_kit.config import RepoConfig, load_repo_config
from repo_kit.errors import RkError
from repo_kit.github import GhCliGitHub, GitHub, PullRequest


def pr_state(repo_root: Path, prs: list[PullRequest], tip: str) -> str:
    """`open`, `merged`, `closed`, `none`, or `newer` when the tip has commits beyond every
    finished PR's head."""
    if not prs:
        return "none"
    if any(pr.state == "open" for pr in prs):
        return "open"
    for state, finished in (
        ("merged", [pr for pr in prs if pr.merged]),
        ("closed", [pr for pr in prs if not pr.merged]),
    ):
        for pr in finished:
            if pr.head_sha and git.is_ancestor(repo_root, tip, pr.head_sha):
                return state
    return "newer"


@dataclass(frozen=True)
class Decision:
    where: str  # "local" or "origin"
    branch: str
    delete: bool
    reason: str

    @property
    def name(self) -> str:
        return self.branch if self.where == "local" else f"origin/{self.branch}"


def plan(repo_root: Path, config: RepoConfig, github: GitHub) -> list[Decision]:
    long_lived = set(config.long_lived_branches)
    checked_out = {wt.branch: wt.path for wt in git.worktree_list(repo_root) if wt.branch}
    main = git.main_checkout(repo_root)
    decisions = []
    for branch in git.local_branches(repo_root):
        if branch in long_lived:
            decisions.append(Decision("local", branch, False, "long-lived"))
        elif branch in checked_out:
            path = checked_out[branch]
            if path.resolve() == main.resolve():
                reason = "checked out"
            else:
                reason = f"checked out in {path}: use `rk wt done`"
            decisions.append(Decision("local", branch, False, reason))
        else:
            state = pr_state(repo_root, github.prs_for_branch(branch), f"refs/heads/{branch}")
            decisions.append(Decision("local", branch, state == "merged", f"PR {state}"))
    for branch in git.remote_branches(repo_root):
        if branch in long_lived:
            continue
        tip = f"refs/remotes/origin/{branch}"
        state = pr_state(repo_root, github.prs_for_branch(branch), tip)
        decisions.append(Decision("origin", branch, state in ("merged", "closed"), f"PR {state}"))
    return decisions


def prune(
    repo_root: Path,
    config: RepoConfig,
    github: GitHub,
    *,
    apply: bool,
    changed: list[str],
    say: Callable[[str], None] = print,
) -> int:
    """List what would go, or delete it. Returns 1 if a deletion was refused."""
    git.fetch(repo_root)
    failed = 0
    decisions = plan(repo_root, config, github)
    for where in ("local", "origin"):
        say(f"== {where}")
        for d in (d for d in decisions if d.where == where):
            if not d.delete:
                say(f"kept          {d.name} ({d.reason})")
            elif not apply:
                say(f"would delete  {d.name} ({d.reason})")
            else:
                ok, detail = _delete(repo_root, d)
                if ok:
                    say(f"deleted       {d.name} ({d.reason})")
                    changed.append(f"deleted {d.name} ({d.reason})")
                else:
                    say(f"FAILED        {d.name}: {detail}")
                    failed += 1
    if not apply:
        say("Dry run. Rerun with --apply to delete.")
    return 1 if failed else 0


def _delete(repo_root: Path, d: Decision) -> tuple[bool, str]:
    if d.where == "origin":
        result = git.delete_remote_branch(repo_root, d.branch)
        return result.ok, result.detail
    try:
        # -D: the work is in the merged PR's head, which the local base may not have yet.
        git.branch_delete(repo_root, d.branch, force=True)
    except git.GitError as exc:
        return False, str(exc)
    return True, ""


def run_prune(
    cwd: Path, *, apply: bool, changed: list[str], say: Callable[[str], None] = print
) -> int:
    """The command, in the repo around `cwd`."""
    repo_root = git.toplevel(cwd)
    url = git.remote_url(repo_root)
    if not git.is_github_url(url):
        raise RkError(f"origin ({url}) isn't on GitHub: branches are pruned by their PRs")
    owner, repo = git.parse_owner_repo(url)
    github = GhCliGitHub(owner, repo)
    return prune(
        repo_root, load_repo_config(repo_root), github, apply=apply, changed=changed, say=say
    )
