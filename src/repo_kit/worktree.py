"""`rk wt start / sync / done / list`: one branch = one worktree = one Claude session.

See docs/worktrees.md. Merge only: nothing here rebases or force-pushes.

Every command takes a `Context`, built once from the current directory. The CLI builds one that
talks to GitHub through `gh`; scenario tests build one around a fake PR store. Each command
records what it changed in `ctx.changed`, which the CLI prints at the end, success or not.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from repo_kit import git
from repo_kit.checks import TYPES
from repo_kit.config import (
    RepoConfig,
    UserConfig,
    load_repo_config,
    load_user_config,
)
from repo_kit.errors import RkError
from repo_kit.github import GhCliGitHub, GitHub, NoGitHub, PullRequest

BRANCH_TYPES = TYPES  # a branch is named for the kind of commit it holds
SLUG_MAX = 40
LOCAL_CONFIG_NAME = ".repo-kit.local.toml"
BASE_CONFIG_KEY = "rkBase"  # git config branch.<name>.rkBase: the base `wt start` used

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_BRANCH = re.compile(rf"^(?P<type>[a-z]+)/{_SEGMENT}(?:/{_SEGMENT})*$")


# --- pure helpers --------------------------------------------------------------------------


def validate_branch(name: str) -> None:
    """Refuse anything that isn't `<type>/<description>` with a Conventional Commits type."""
    match = _BRANCH.match(name)
    types = ", ".join(BRANCH_TYPES)
    if not match:
        raise RkError(
            f"invalid branch name {name!r}: use <type>/<description>, such as feat/api-v2 "
            f"(letters, digits, '.', '_', '-', and '/' between parts)"
        )
    if match["type"] not in BRANCH_TYPES:
        raise RkError(f"invalid branch type {match['type']!r} in {name!r}: use one of {types}")
    if ".." in name or name.endswith((".", ".lock")) or "/." in name or ".lock/" in name:
        raise RkError(f"invalid branch name {name!r}: Git doesn't allow it")


def slug(branch: str) -> str:
    """The branch name with unsafe characters replaced by `-`, cut to 40 characters."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", branch)
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    return text[:SLUG_MAX].rstrip("-.")


def hash6(branch: str) -> str:
    """First 6 hex characters of SHA-1 of the full branch name: keeps equal slugs apart."""
    return hashlib.sha1(branch.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]


def repo_dir(root: Path, owner: str, repo: str) -> Path:
    return root / f"{owner}-{repo}"


def worktree_path(root: Path, owner: str, repo: str, branch: str) -> Path:
    return repo_dir(root, owner, repo) / f"{slug(branch)}-{hash6(branch)}"


def parse_links(links: Sequence[str]) -> dict[str, str]:
    """`["pkg=feat/x", ...]` -> {"pkg": "feat/x"}."""
    result: dict[str, str] = {}
    for link in links:
        pkg, sep, branch = link.partition("=")
        if not sep or not pkg or not branch:
            raise RkError(f"invalid --link {link!r}: use <pkg>=<branch>")
        if pkg in result:
            raise RkError(f"--link {pkg} given twice")
        result[pkg] = branch
    return result


def bash() -> str:
    """The bash to run `.sh` scripts with. On Windows that's Git Bash: a bare `bash` would reach
    `System32\\bash.exe` (WSL) first, because Windows searches System32 before PATH."""
    if os.name != "nt":
        return "bash"
    git_exe = shutil.which("git")
    if git_exe:
        # <Git>/cmd/git.exe or <Git>/mingw64/bin/git.exe -> <Git>/bin/bash.exe
        for parent in Path(git_exe).resolve().parents:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("bash")
    windir = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")).resolve()
    if found and not Path(found).resolve().is_relative_to(windir):
        return found
    raise RkError("Git Bash not found: install Git for Windows to run .sh scripts")


def command_args(command: str) -> list[str]:
    """A `[worktree]` setup/check string as an argument list. A `.sh` script runs through `bash`
    (Git Bash on Windows); on Windows other programs are resolved on PATH so `.exe`/`.cmd` work."""
    args = shlex.split(command)
    if not args:
        raise RkError(f"empty command in [worktree]: {command!r}")
    if args[0].endswith(".sh"):
        return [bash(), *args]
    if os.name == "nt":
        found = shutil.which(args[0])
        if found:
            args[0] = found
    return args


# --- context -------------------------------------------------------------------------------


@dataclass
class Context:
    cwd: Path
    main: Path  # the main checkout
    owner: str
    repo: str
    repo_config: RepoConfig
    user_config: UserConfig
    github: GitHub
    changed: list[str] = field(default_factory=list)
    say: Callable[[str], None] = field(default=lambda msg: print(msg, flush=True))

    @classmethod
    def load(
        cls,
        cwd: Path,
        github: GitHub | None = None,
        user_config: UserConfig | None = None,
    ) -> Context:
        git.require_version(cwd)
        main = git.main_checkout(cwd)
        url = git.remote_url(main)
        owner, repo = git.parse_owner_repo(url)
        if github is None:
            github = GhCliGitHub(owner, repo) if git.is_github_url(url) else NoGitHub()
        return cls(
            cwd=cwd,
            main=main,
            owner=owner,
            repo=repo,
            repo_config=load_repo_config(main),
            user_config=user_config or load_user_config(),
            github=github,
        )

    @property
    def repo_root_dir(self) -> Path:
        return repo_dir(self.user_config.worktree_root, self.owner, self.repo)

    def path_for(self, branch: str) -> Path:
        return worktree_path(self.user_config.worktree_root, self.owner, self.repo, branch)


def _run_configured(command: str, cwd: Path, what: str) -> None:
    args = command_args(command)
    try:
        code = subprocess.run(args, cwd=cwd).returncode
    except FileNotFoundError:
        raise RkError(f"{what} `{command}`: {args[0]} not found on PATH") from None
    if code != 0:
        raise RkError(f"{what} `{command}` failed (exit {code}) in {cwd}")


def _resolve_branch(ctx: Context, branch: str | None) -> str:
    if branch:
        return branch
    current = git.current_branch(ctx.cwd)
    if not current:
        raise RkError("not on a branch: name the branch, or run this inside its worktree")
    return current


def _inside(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


# --- wt start ------------------------------------------------------------------------------


def start(
    ctx: Context,
    branch: str,
    from_branch: str | None = None,
    links: Sequence[str] = (),
    no_setup: bool = False,
) -> Path:
    validate_branch(branch)
    wanted_links = parse_links(links)
    path = ctx.path_for(branch)

    git.fetch(ctx.main)
    if git.local_branch_exists(ctx.main, branch) or git.remote_branch_exists(ctx.main, branch):
        raise RkError(f"branch {branch} already exists: use its worktree, or pick another name")
    if path.exists():
        raise RkError(f"{path} already exists: remove it or pick another branch name")

    base = from_branch or ctx.repo_config.default_base
    if git.remote_branch_exists(ctx.main, base):
        start_point = f"origin/{base}"
    elif from_branch and git.local_branch_exists(ctx.main, base):
        start_point = base  # chaining on a branch that isn't pushed yet
    else:
        raise RkError(f"base branch {base} not found on origin")

    # Resolve every link before creating anything, so a bad link leaves nothing behind.
    link_paths: dict[str, Path] = {}
    for pkg, pkg_branch in wanted_links.items():
        target = worktree_path(ctx.user_config.worktree_root, ctx.owner, pkg, pkg_branch)
        if not target.is_dir():
            raise RkError(
                f"--link {pkg}={pkg_branch}: no worktree at {target}. "
                f"Run `rk wt start {pkg_branch}` in {pkg} first."
            )
        link_paths[pkg] = target

    path.parent.mkdir(parents=True, exist_ok=True)
    git.worktree_add(ctx.main, path, branch, start_point)
    ctx.changed.append(f"created branch {branch} from {start_point}")
    ctx.changed.append(f"added worktree {path}")
    git.config_set(ctx.main, f"branch.{branch}.{BASE_CONFIG_KEY}", base)

    if link_paths:
        _write_links(ctx, path, link_paths)

    if not no_setup:
        for command in load_repo_config(path).worktree.setup:
            _run_configured(command, path, "setup")
            ctx.changed.append(f"ran setup `{command}`")

    ctx.say(f"Worktree ready: {path}")
    ctx.say("Start Claude in it:")
    ctx.say(f'  cd "{path}"')
    ctx.say("  claude")
    return path


def _write_links(ctx: Context, path: Path, link_paths: dict[str, Path]) -> None:
    lines = [
        "# Written by `rk wt start --link`. Local to this worktree; `rk wt done` removes it.",
        "[links]",
        *(f'"{pkg}" = "{target.as_posix()}"' for pkg, target in link_paths.items()),
    ]
    (path / LOCAL_CONFIG_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.changed.append(f"wrote {LOCAL_CONFIG_NAME} linking {', '.join(link_paths)}")
    if not git.is_ignored(path, LOCAL_CONFIG_NAME):
        exclude = git.common_dir(path) / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with exclude.open("a", encoding="utf-8") as f:
            f.write(f"{prefix}{LOCAL_CONFIG_NAME}\n")
        ctx.changed.append(f"added {LOCAL_CONFIG_NAME} to {exclude}")


# --- wt sync -------------------------------------------------------------------------------


def _worktree_of(ctx: Context, branch: str) -> git.Worktree:
    wt = git.worktree_for_branch(ctx.main, branch)
    if wt is None:
        raise RkError(f"{branch} isn't checked out in any worktree of {ctx.owner}/{ctx.repo}")
    return wt


def _merge(ctx: Context, cwd: Path, branch: str, ref: str) -> None:
    result = git.merge(cwd, ref)
    if result.conflicts:
        files = "\n".join(f"  {name}" for name in result.conflicts)
        raise RkError(
            f"merging {ref} into {branch} conflicted in:\n{files}\n"
            "Resolve each file, commit the merge, then run `rk wt sync` again. Nothing was pushed."
        )
    if not result.up_to_date:
        ctx.changed.append(f"merged {ref} into {branch}")


def sync(ctx: Context, branch: str | None = None) -> None:
    branch = _resolve_branch(ctx, branch)
    wt = _worktree_of(ctx, branch)
    config = load_repo_config(wt.path)

    dirty = git.status_porcelain(wt.path, exclude=config.worktree.env_dirs)
    if dirty or git.merge_in_progress(wt.path):
        listing = "\n".join(f"  {line}" for line in dirty[:20]) or "  (a merge is in progress)"
        raise RkError(
            f"{branch} has uncommitted or untracked changes; commit them first:\n{listing}"
        )

    git.fetch(wt.path)
    pr = ctx.github.pr_for_branch(branch)
    if (
        pr
        and git.config_get(wt.path, f"branch.{branch}.merge")
        and not git.remote_branch_exists(wt.path, branch)
        and _pr_finishes(wt.path, branch, pr)
    ):
        raise RkError(
            f"origin/{branch} is gone and PR #{pr.number} is {pr_state(pr)}, so {branch} is "
            f"finished: not pushing it back. Run `rk wt done {branch}` to remove it."
        )
    remote_before = (
        git.out(["rev-parse", f"origin/{branch}"], wt.path)
        if git.remote_branch_exists(wt.path, branch)
        else None
    )
    if remote_before:
        _merge(ctx, wt.path, branch, f"origin/{branch}")

    if pr and pr.state == "open":
        base, why = pr.base_ref, f"the base of PR #{pr.number}"
    elif recorded := git.config_get(wt.path, f"branch.{branch}.{BASE_CONFIG_KEY}"):
        base, why = recorded, "the base it was started from (no open PR)"
    else:
        base, why = config.default_base, f"the {config.profile} default (no open PR)"
    if not git.remote_branch_exists(wt.path, base):
        raise RkError(f"base branch {base} ({why}) not found on origin")
    ctx.say(f"Merging origin/{base}: {why}")
    _merge(ctx, wt.path, branch, f"origin/{base}")

    if config.worktree.check:
        try:
            _run_configured(config.worktree.check, wt.path, "check")
        except RkError as exc:
            # A worktree started with --no-setup, or before [worktree].setup existed, has no env:
            # the check fails for that reason, not because of the merge.
            missing = [d for d in config.worktree.env_dirs if not (wt.path / d).is_dir()]
            if not (config.worktree.setup and missing):
                raise
            steps = ", ".join(f"`{step}`" for step in config.worktree.setup)
            raise RkError(
                f"{exc}.\n{missing[0]} is missing, so this worktree was likely started without "
                f"its setup. Run it in {wt.path}: {steps}, then `rk wt sync` again. "
                "Nothing was pushed."
            ) from None

    head = git.out(["rev-parse", "HEAD"], wt.path)
    if head != remote_before:
        result = git.push(wt.path, branch)
        if not result.ok:
            raise RkError(
                f"push of {branch} was rejected (origin/{branch} moved). "
                f"Run `rk wt sync` again.\n{result.detail}"
            )
        ctx.changed.append(f"pushed {branch} to origin")

    label = config.update_prs.conflict_label
    if pr and pr.state == "open" and label in pr.labels:
        ctx.github.remove_label(pr.number, label)
        ctx.changed.append(f"removed {label} from PR #{pr.number}")
    ctx.say(f"{branch} is in sync with origin/{base}")


# --- wt done -------------------------------------------------------------------------------


def _force_remove_readonly(func: Callable[[str], object], path: str, _exc: object) -> None:
    # Windows: Git objects and some venv files are read-only, which stops rmtree.
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, onexc=_force_remove_readonly)


# Regenerable tool caches: ignored by Git, but not work worth refusing `wt done` over.
_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_REMOVING_MARKER = "rk-removing"


def _pr_finishes(cwd: Path, branch: str, pr: PullRequest) -> bool:
    """Whether `pr` (merged or closed) finished this `branch`: the branch's current head must be
    in the PR's head, or an old PR of a reused branch name would count for new work."""
    return (
        pr.finished
        and bool(pr.head_sha)
        and git.is_ancestor(cwd, f"refs/heads/{branch}", pr.head_sha)
    )


def _nothing_beyond_base(cwd: Path, branch: str, config: RepoConfig) -> bool:
    """Whether `branch` has no commits of its own: its head is already in origin's copy of the
    base it was started from (or the profile's default). Such a branch needs no PR to be
    finished, since removing it loses no commit."""
    base = git.config_get(cwd, f"branch.{branch}.{BASE_CONFIG_KEY}") or config.default_base
    return git.remote_branch_exists(cwd, base) and git.is_ancestor(
        cwd, f"refs/heads/{branch}", f"refs/remotes/origin/{base}"
    )


def _ignored_outside(ignored: Sequence[str], env_dirs: Sequence[str]) -> list[str]:
    """The ignored paths that rk wouldn't remove anyway: not in an env dir, a cache or its own
    links file."""
    envs = [name.replace("\\", "/").strip("/") for name in env_dirs]
    kept = []
    for path in ignored:
        bare = path.rstrip("/")
        parts = bare.split("/")
        if bare == LOCAL_CONFIG_NAME or any(
            p in _CACHE_DIRS or p.endswith(".egg-info") for p in parts
        ):
            continue
        if any(bare == env or bare.startswith(env + "/") for env in envs):
            continue
        kept.append(path)
    return kept


def _remove_env_dir(env_dir: Path) -> None:
    try:
        _rmtree(env_dir)
    except OSError as exc:
        locked = exc.filename or env_dir
        raise RkError(
            f"couldn't remove {locked}: {exc.strerror or exc}.\n"
            f"Close whatever is using {env_dir} (a terminal, editor or running Python), then run "
            "`rk wt done` again. Nothing else was removed."
        ) from exc


def done(
    ctx: Context,
    branch: str | None = None,
    allow_unmerged: bool = False,
    discard_changes: bool = False,
    delete_unpushed: bool = False,
) -> None:
    branch = _resolve_branch(ctx, branch)
    wt = git.worktree_for_branch(ctx.main, branch)
    if wt is None:
        raise RkError(f"{branch} has no worktree in {ctx.owner}/{ctx.repo}: nothing to remove")
    if wt.path.resolve() == ctx.main.resolve():
        raise RkError(f"{branch} is checked out in the main checkout, which rk never removes")
    if not _inside(wt.path, ctx.repo_root_dir):
        raise RkError(
            f"the worktree for {branch} ({wt.path}) isn't under {ctx.repo_root_dir}, so rk "
            "didn't create it for this repo. Remove it with `git worktree remove` if intended."
        )
    if _inside(ctx.cwd, wt.path):
        raise RkError(
            f"run `rk wt done` from outside the worktree being removed, "
            f"such as the main checkout: {ctx.main}"
        )

    git.fetch(ctx.main)
    problems: list[str] = []
    pr = ctx.github.pr_for_branch(branch)
    config = load_repo_config(wt.path)
    if (
        not allow_unmerged
        and not (pr and _pr_finishes(ctx.main, branch, pr))
        and not (pr is None and _nothing_beyond_base(ctx.main, branch, config))
    ):
        if pr is None:
            what = "no PR was found"
        elif pr.finished:
            what = (
                f"PR #{pr.number} is {pr_state(pr)} but doesn't contain the latest commit of "
                f"{branch} (an older PR of the same branch name?)"
            )
        else:
            what = f"PR #{pr.number} is still open"
        problems.append(f"{what} (--allow-unmerged removes it anyway)")
    unpushed = git.unpushed_commits(ctx.main, branch)
    if unpushed and not delete_unpushed:
        problems.append(
            f"{len(unpushed)} unpushed commit(s) (--delete-unpushed loses them):\n"
            + "\n".join(f"      {line}" for line in unpushed[:20])
        )

    # A directory already gone means an earlier run got that far: only Git's record is left.
    present = wt.path.is_dir()
    if present:
        for name in config.worktree.env_dirs:
            tracked = git.tracked_files(wt.path, name.replace("\\", "/").strip("/"))
            if tracked:
                raise RkError(
                    f"refusing to remove {branch}: `[worktree].env_dirs` entry {name!r} holds "
                    f"{len(tracked)} file(s) Git tracks (such as {tracked[0]}), and rk deletes env "
                    "dirs. Take it out of env_dirs in .repo-kit.toml."
                )
        # The env dirs are rk's to remove: an untracked .venv is not work that could be lost.
        dirty = git.status_porcelain(wt.path, exclude=config.worktree.env_dirs)
        if git.git_path(wt.path, _REMOVING_MARKER).exists():
            # An earlier run started removing and stopped: the files it deleted show as deleted.
            dirty = [line for line in dirty if not line.startswith((" D", "D "))]
        if dirty and not discard_changes:
            problems.append(
                f"{len(dirty)} uncommitted or untracked change(s) (--discard-changes loses "
                "them):\n" + "\n".join(f"      {line}" for line in dirty[:20])
            )
        ignored = _ignored_outside(git.ignored_paths(wt.path), config.worktree.env_dirs)
        if ignored:
            problems.append(
                f"{len(ignored)} ignored file(s) that removing the worktree would delete (move "
                "or delete them first; there is no override):\n"
                + "\n".join(f"      {line}" for line in ignored[:20])
            )
    if problems:
        listing = "\n".join(f"  - {p}" for p in problems)
        raise RkError(f"refusing to remove {branch}:\n{listing}")

    # Every check has passed. Remove the parts that fail on Windows file locks first, and leave a
    # marker before the step that deletes tracked files, so a rerun after a failure there is clean.
    if present:
        links = wt.path / LOCAL_CONFIG_NAME
        if links.exists():
            links.unlink()
            ctx.changed.append(f"removed links ({LOCAL_CONFIG_NAME})")
        for name in config.worktree.env_dirs:
            env_dir = wt.path / name
            if env_dir.is_dir() and _inside(env_dir, wt.path):
                _remove_env_dir(env_dir)
                ctx.changed.append(f"removed {env_dir}")
        git.git_path(wt.path, _REMOVING_MARKER).write_text("removing\n", encoding="utf-8")
        try:
            git.worktree_remove(ctx.main, wt.path, force=True)
        except git.GitError as exc:
            raise RkError(
                f"couldn't remove the worktree {wt.path}; something may be using a file in it "
                "(an editor, terminal or running program). Close it, then run `rk wt done` again; "
                f"files already deleted from it are expected.\n{exc}"
            ) from exc
        ctx.changed.append(f"removed worktree {wt.path}")
    git.worktree_prune(ctx.main)
    # -D, not -d: whether the work is safe was checked above against every remote ref (a merged
    # PR's commits live on its base), which `-d` can't see from the main checkout's HEAD.
    git.branch_delete(ctx.main, branch, force=True)
    ctx.changed.append(f"deleted local branch {branch}")


# --- wt list (and `rk doctor`'s stale worktrees) -------------------------------------------


def rk_worktrees(cwd: Path, root: Path) -> list[tuple[Path, str]]:
    """(path, branch) of each worktree of the repo at `cwd` that is under `root` (this repo's
    `<worktree_root>/<owner>-<repo>`) and on a branch. The main checkout is never under it."""
    root = root.resolve()
    return [
        (wt.path, wt.branch)
        for wt in git.worktree_list(cwd)
        if wt.branch and wt.path.resolve().is_relative_to(root)
    ]


def prs_for(branches: Sequence[str], github: GitHub) -> list[PullRequest | None]:
    """Each branch's PR (the open one, else the most recent), in order. Raises `RkError` on the
    first lookup that fails."""
    return [github.pr_for_branch(branch) for branch in branches]


def pr_state(pr: PullRequest | None) -> str:
    """open, draft, merged, closed, or none."""
    if pr is None:
        return "none"
    if pr.merged:
        return "merged"
    if pr.state == "closed":
        return "closed"
    return "draft" if pr.draft else "open"


@dataclass(frozen=True)
class WorktreeStatus:
    path: Path
    branch: str
    pr: PullRequest | None
    uncommitted: int | None  # None: the directory is gone
    unpushed: int


def _uncommitted(ctx: Context, path: Path) -> int | None:
    if not path.is_dir():
        return None
    try:
        env_dirs = load_repo_config(path).worktree.env_dirs
    except RkError:
        env_dirs = ctx.repo_config.worktree.env_dirs
    return len(git.status_porcelain(path, exclude=env_dirs))


def worktree_statuses(ctx: Context) -> tuple[list[WorktreeStatus], str]:
    """Every worktree of this repo under its worktree root, and a note when the PRs are unknown
    (empty when they're known). Reads only: nothing is written or fetched."""
    ours = rk_worktrees(ctx.main, ctx.repo_root_dir)
    note = ""
    prs: list[PullRequest | None] = [None] * len(ours)
    if isinstance(ctx.github, NoGitHub):
        note = "PRs unknown: origin isn't on GitHub"
    else:
        try:
            prs = prs_for([branch for _, branch in ours], ctx.github)
        except RkError as exc:
            note = f"PRs unknown: can't look them up\n{exc}"
    statuses = [
        WorktreeStatus(
            path=path,
            branch=branch,
            pr=pr,
            uncommitted=_uncommitted(ctx, path),
            unpushed=len(git.unpushed_commits(ctx.main, branch)),
        )
        for (path, branch), pr in zip(ours, prs, strict=True)
    ]
    return statuses, note


def render_list(statuses: Sequence[WorktreeStatus], prs_known: bool) -> list[str]:
    """One aligned line per worktree, under a header."""
    rows = [("BRANCH", "PR", "LOCAL", "PATH")]
    for st in statuses:
        if st.pr is not None:
            pr = f"#{st.pr.number} {pr_state(st.pr)}"
        else:
            pr = "none" if prs_known else "?"
        flags = [
            "directory missing" if st.uncommitted is None else "",
            f"{st.uncommitted} uncommitted" if st.uncommitted else "",
            f"{st.unpushed} unpushed" if st.unpushed else "",
        ]
        local = ", ".join(f for f in flags if f) or "clean"
        rows.append((st.branch, pr, local, str(st.path)))
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    return [
        "  ".join(
            [*(cell.ljust(width) for cell, width in zip(row[:3], widths, strict=True)), row[3]]
        )
        for row in rows
    ]


def list_worktrees(ctx: Context) -> int:
    """The command: print this repo's worktrees. Always exits 0."""
    statuses, note = worktree_statuses(ctx)
    if not statuses:
        ctx.say(f"No worktrees under {ctx.repo_root_dir}")
    else:
        for line in render_list(statuses, prs_known=not note):
            ctx.say(line)
    for line in note.splitlines():
        ctx.say(line)
    return 0
