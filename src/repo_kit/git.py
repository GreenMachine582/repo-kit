"""Thin wrappers over the `git` CLI.

List-argument `subprocess` calls and `pathlib` paths only, so they behave the same on Windows.
Nothing here rebases or force-pushes: there is deliberately no wrapper that could.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from repo_kit.errors import RkError

# The oldest Git rk supports. Everything rk runs (`worktree add -b`, `worktree list --porcelain`,
# `merge --no-edit`, `rev-list --not --remotes`, `push -u`) works well before this; 2.38 is the
# floor we test against and `doctor` reports.
MIN_GIT = (2, 38)


class GitError(RkError):
    """A git command failed."""


def run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run `git <args>` in `cwd` and capture its output."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise GitError(f"`git {' '.join(args)}` failed in {cwd}:\n{detail}")
    return proc


def out(args: list[str], cwd: Path) -> str:
    return run(args, cwd).stdout.strip()


# --- version -------------------------------------------------------------------------------


def parse_version(text: str) -> tuple[int, int]:
    """`git version 2.47.1.windows.1` -> (2, 47)."""
    match = re.search(r"(\d+)\.(\d+)", text)
    if not match:
        raise GitError(f"can't read the Git version from {text!r}")
    return int(match[1]), int(match[2])


def version(cwd: Path) -> tuple[int, int]:
    try:
        return parse_version(out(["--version"], cwd))
    except FileNotFoundError as exc:
        raise GitError("git is not on PATH") from exc


def require_version(cwd: Path) -> None:
    found = version(cwd)
    if found < MIN_GIT:
        need = ".".join(map(str, MIN_GIT))
        have = ".".join(map(str, found))
        raise GitError(f"rk needs Git {need} or newer; found {have}")


# --- repository ----------------------------------------------------------------------------


def toplevel(cwd: Path) -> Path:
    return Path(out(["rev-parse", "--show-toplevel"], cwd))


def common_dir(cwd: Path) -> Path:
    """The shared `.git` directory, the same from the main checkout and every worktree."""
    return Path(out(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd))


def main_checkout(cwd: Path) -> Path:
    return common_dir(cwd).parent


def current_branch(cwd: Path) -> str | None:
    proc = run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd, check=False)
    return proc.stdout.strip() or None


def remote_url(cwd: Path, remote: str = "origin") -> str:
    return out(["remote", "get-url", remote], cwd)


_GITHUB_URL = re.compile(r"github\.com[:/]+(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def is_github_url(url: str) -> bool:
    return _GITHUB_URL.search(url) is not None


def parse_owner_repo(url: str) -> tuple[str, str]:
    """`https://github.com/o/r.git` or `git@github.com:o/r.git` -> ("o", "r").

    Anything else (a local path, as in the scenario tests) is owner `local` and the last path
    component, without `.git`.
    """
    match = _GITHUB_URL.search(url)
    if match:
        return match["owner"], match["repo"]
    name = re.split(r"[\\/]", url.rstrip("/\\"))[-1]
    return "local", name.removesuffix(".git")


def fetch(cwd: Path, remote: str = "origin") -> None:
    run(["fetch", "--prune", remote], cwd)


def ref_exists(cwd: Path, ref: str) -> bool:
    proc = run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd, check=False)
    return proc.returncode == 0


def local_branch_exists(cwd: Path, branch: str) -> bool:
    return ref_exists(cwd, f"refs/heads/{branch}")


def remote_branch_exists(cwd: Path, branch: str, remote: str = "origin") -> bool:
    return ref_exists(cwd, f"refs/remotes/{remote}/{branch}")


def create_remote_branch(cwd: Path, branch: str, start_ref: str, remote: str = "origin") -> None:
    """Create `branch` on the remote at `start_ref`. A plain push, so it never moves a branch
    that is already there."""
    if (
        run(["ls-remote", "--exit-code", "--heads", remote, branch], cwd, check=False).returncode
        == 0
    ):
        raise GitError(f"{remote} already has a `{branch}` branch")
    run(["push", remote, f"{start_ref}:refs/heads/{branch}"], cwd)


def config_get(cwd: Path, key: str) -> str | None:
    proc = run(["config", "--get", key], cwd, check=False)
    return proc.stdout.strip() or None


def config_set(cwd: Path, key: str, value: str) -> None:
    run(["config", key, value], cwd)


def is_ignored(cwd: Path, path: str) -> bool:
    return run(["check-ignore", "--quiet", "--no-index", path], cwd, check=False).returncode == 0


# --- worktrees -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str | None  # None for a detached HEAD
    bare: bool = False


def worktree_list(cwd: Path) -> list[Worktree]:
    result: list[Worktree] = []
    path: Path | None = None
    branch: str | None = None
    bare = False
    for line in [*out(["worktree", "list", "--porcelain"], cwd).splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch "):
            branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        elif line == "bare":
            bare = True
        elif not line and path is not None:
            result.append(Worktree(path, branch, bare))
            path, branch, bare = None, None, False
    return result


def worktree_for_branch(cwd: Path, branch: str) -> Worktree | None:
    return next((wt for wt in worktree_list(cwd) if wt.branch == branch), None)


def worktree_add(cwd: Path, path: Path, branch: str, start_point: str) -> None:
    # --no-track: the new branch must not track its base, or a plain `git push` would push to it.
    run(["worktree", "add", "--no-track", "-b", branch, str(path), start_point], cwd)


def worktree_remove(cwd: Path, path: Path, force: bool = False) -> None:
    run(["worktree", "remove", *(["--force"] if force else []), str(path)], cwd)


def worktree_prune(cwd: Path) -> None:
    run(["worktree", "prune"], cwd)


def branch_delete(cwd: Path, branch: str, force: bool = False) -> None:
    run(["branch", "-D" if force else "-d", branch], cwd)


# --- working tree state --------------------------------------------------------------------


def status_porcelain(cwd: Path, exclude: tuple[str, ...] = ()) -> list[str]:
    """Uncommitted and untracked changes, one line each. Ignored files and the `exclude` paths
    (such as `[worktree].env_dirs`) don't count."""
    pathspecs = ["--", ".", *(f":(exclude){path}" for path in exclude)] if exclude else []
    proc = run(["status", "--porcelain", *pathspecs], cwd)
    return [line for line in proc.stdout.splitlines() if line]


def merge_in_progress(cwd: Path) -> bool:
    return ref_exists(cwd, "MERGE_HEAD")


def log_subjects(cwd: Path, revisions: str) -> list[tuple[str, str]]:
    """(short sha, first line) of each non-merge commit in `revisions`, newest first."""
    proc = run(["log", "--no-merges", "--format=%h%x00%s", revisions, "--"], cwd)
    return [
        (sha, subject)
        for sha, _, subject in (line.partition("\x00") for line in proc.stdout.splitlines() if line)
    ]


def git_path(cwd: Path, name: str) -> Path:
    """Where Git keeps `name` (such as `hooks/commit-msg`), honouring `core.hooksPath`."""
    path = Path(out(["rev-parse", "--git-path", name], cwd))
    return path if path.is_absolute() else cwd / path


def unpushed_commits(cwd: Path, branch: str) -> list[str]:
    """Commits on `branch` that no remote-tracking ref contains, as `<sha> <subject>`."""
    proc = run(["log", "--format=%h %s", branch, "--not", "--remotes"], cwd)
    return [line for line in proc.stdout.splitlines() if line]


@dataclass(frozen=True)
class MergeResult:
    merged: bool
    conflicts: tuple[str, ...] = ()
    up_to_date: bool = False


def merge(cwd: Path, ref: str) -> MergeResult:
    """Merge `ref` into the current branch with a merge commit. On a conflict the merge is left in
    progress, for the person (or Claude) to resolve."""
    before = out(["rev-parse", "HEAD"], cwd)
    proc = run(["merge", "--no-edit", ref], cwd, check=False)
    if proc.returncode == 0:
        return MergeResult(merged=True, up_to_date=out(["rev-parse", "HEAD"], cwd) == before)
    conflicts = tuple(
        line for line in out(["diff", "--name-only", "--diff-filter=U"], cwd).splitlines() if line
    )
    if not conflicts:
        detail = (proc.stderr or proc.stdout).strip()
        raise GitError(f"`git merge {ref}` failed in {cwd}:\n{detail}")
    return MergeResult(merged=False, conflicts=conflicts)


@dataclass(frozen=True)
class PushResult:
    ok: bool
    detail: str = ""


def push(cwd: Path, branch: str, remote: str = "origin") -> PushResult:
    """Push without force. A rejection (the remote moved) is a result, not an error."""
    proc = run(["push", "-u", remote, f"{branch}:{branch}"], cwd, check=False)
    return PushResult(proc.returncode == 0, (proc.stderr or proc.stdout).strip())
