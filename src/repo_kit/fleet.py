"""`rk fleet`: `doctor`, `sync --check`, `configure` and `upgrade` across every adopting repo.

The repos are `[fleet.repos]` in the user config: `owner/name` -> this machine's checkout. Each
command calls rk's own functions once per repo with that checkout as `cwd` (never a subprocess
`rk`, so every repo runs the engine that is running now), catches that repo's failure, and goes on
to the next. The result is one table and an exit code of 1 if any repo failed.

Nothing here touches a checkout's working tree except `configure`'s own writes to GitHub:
`upgrade` works in a throwaway worktree cut from `origin/<base>`, so a dirty or busy checkout is no
obstacle. `github apply` is not offered: it never runs outside a person's own terminal, one repo
at a time. See docs/fleet.md.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import repo_kit
from repo_kit import bootstrap, checks, doctor, files, git
from repo_kit.config import RepoConfig, UserConfig, load_repo_config
from repo_kit.errors import RkError
from repo_kit.github import GhCliGitHub, GitHub


class Status(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"
    PLAN = "PLAN"  # an upgrade that `--apply` would open a PR for
    PR = "PR"  # an upgrade PR that is open now


@dataclass(frozen=True)
class RepoResult:
    slug: str
    status: Status
    detail: str = ""


GitHubFor = Callable[[str, str], GitHub]
Say = Callable[[str], None]


def _github_for(owner: str, repo: str) -> GitHub:
    return GhCliGitHub(owner, repo)


# --- picking the repos ---------------------------------------------------------------------


def select(user_config: UserConfig, only: Sequence[str]) -> dict[str, Path]:
    """The repos to run on, in config order: all of `[fleet.repos]`, or just `only`."""
    repos = user_config.fleet.repos
    if not repos:
        raise RkError(
            "no repos to run on: list them in the user config, such as\n"
            '  [fleet.repos]\n  "GreenMachine582/PyFinBot" = "~/dev/PyFinBot"'
        )
    wanted = {slug.lower(): slug for slug in only}
    unknown = [slug for slug in only if slug.lower() not in {s.lower() for s in repos}]
    if unknown:
        raise RkError(f"not in [fleet.repos]: {', '.join(unknown)}")
    if not wanted:
        return dict(repos)
    return {slug: path for slug, path in repos.items() if slug.lower() in wanted}


def locate(slug: str, path: Path) -> Path:
    """The main checkout at `path`, once it is shown to be `slug`'s: a wrong path would run
    rk, and for `upgrade` push a branch, to the wrong repo."""
    if not path.is_dir():
        raise RkError(f"{path} isn't a folder")
    git.require_version(path)
    try:
        root = git.main_checkout(path)
    except RkError:
        raise RkError(f"{path} isn't a Git checkout") from None
    owner, repo = git.parse_owner_repo(git.remote_url(root))
    if f"{owner}/{repo}".lower() != slug.lower():
        raise RkError(f"{path}: origin is {owner}/{repo}, not {slug}")
    return root


def _each(
    repos: dict[str, Path], one: Callable[[str, Path], RepoResult], say: Say | None = None
) -> list[RepoResult]:
    """`one` for every repo; one repo's failure is its row, never the end of the run."""
    results = []
    for slug, path in repos.items():
        if say:
            say(f"== {slug}")
        try:
            result = one(slug, path)
        except RkError as exc:
            result = RepoResult(slug, Status.FAIL, str(exc))
        results.append(result)
    return results


# --- the table -----------------------------------------------------------------------------


def render(results: Sequence[RepoResult]) -> str:
    width = max((len(r.slug) for r in results), default=4)
    lines = []
    for r in results:
        first, *rest = (r.detail or "").splitlines() or [""]
        lines.append(f"{r.slug:<{width}}  {r.status:<5}  {first}".rstrip())
        lines += [f"{'':<{width}}  {'':<5}  {line}".rstrip() for line in rest]
    counts = ", ".join(
        f"{sum(r.status is s for r in results)} {s.lower()}"
        for s in Status
        if any(r.status is s for r in results)
    )
    lines.append(f"{len(results)} repo(s): {counts or 'none'}")
    return "\n".join(lines)


def failed(results: Sequence[RepoResult]) -> bool:
    return any(r.status is Status.FAIL for r in results)


def _finish(results: list[RepoResult], say: Say) -> int:
    say(render(results))
    return 1 if failed(results) else 0


# --- doctor --------------------------------------------------------------------------------

DoctorRunner = Callable[[Path], tuple[int, str]]
_NAMES_SHOWN = 4


def _run_doctor_json(path: Path) -> tuple[int, str]:
    """`rk doctor --json` for the checkout at `path`: its exit code and its one document."""
    lines: list[str] = []
    code = doctor.run_doctor(path, as_json=True, say=lines.append)
    return code, "\n".join(lines)


def summarise_doctor(text: str) -> tuple[Status, str]:
    """A repo's row from `rk doctor --json`. Reads only `ok`, and each check's `name` and
    `status`, so a document that gains keys still reads."""
    try:
        document = json.loads(text)
        found = [(c["name"], c["status"]) for c in document["checks"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise RkError(f"doctor's output wasn't the document expected ({exc!r})") from exc
    failing = [n for n, s in found if s == "FAIL"]
    warning = [n for n, s in found if s == "WARN"]
    parts = []
    for label, names in (("FAIL", failing), ("WARN", warning)):
        if names:
            shown = ", ".join(names[:_NAMES_SHOWN])
            more = f" (+{len(names) - _NAMES_SHOWN})" if len(names) > _NAMES_SHOWN else ""
            parts.append(f"{label}: {shown}{more}")
    status = Status.FAIL if failing else Status.WARN if warning else Status.OK
    summary = f"{len(found)} check(s)" + ("; " + "; ".join(parts) if parts else "")
    return status, summary


def run_doctor(
    user_config: UserConfig,
    only: Sequence[str] = (),
    *,
    runner: DoctorRunner = _run_doctor_json,
    say: Say = print,
) -> int:
    """`rk fleet doctor`: `rk doctor` in every repo, one row each. Read-only."""

    def one(slug: str, path: Path) -> RepoResult:
        _code, text = runner(locate(slug, path))
        status, detail = summarise_doctor(text)
        return RepoResult(slug, status, detail)

    return _finish(_each(select(user_config, only), one), say)


# --- sync --check --------------------------------------------------------------------------


def run_sync_check(
    user_config: UserConfig,
    only: Sequence[str] = (),
    *,
    templates: Path | None = None,
    say: Say = print,
) -> int:
    """`rk fleet sync --check`: is each repo's checkout what its pinned version renders? Writes
    nothing. A pin that trails this rk is a failure too: `rk fleet upgrade` moves it."""

    def one(slug: str, path: Path) -> RepoResult:
        root = locate(slug, path)
        git.require_version(root)
        config = load_repo_config(root)
        files.require_pin(config)
        owner, repo = git.parse_owner_repo(git.remote_url(root))
        plan = files.plan_sync(root, config, owner, repo, templates)
        if not plan.drifted:
            return RepoResult(slug, Status.OK, "in sync")
        writes = (files.Action.CREATE, files.Action.UPDATE, files.Action.REFUSE)
        paths = [f"{f.path} ({f.action})" for f in plan.files if f.action in writes]
        if plan.lock_changed:
            paths.append(f"{files.LOCK_NAME} (stale)")
        return RepoResult(slug, Status.FAIL, "out of sync: " + ", ".join(paths))

    return _finish(_each(select(user_config, only), one), say)


# --- configure -----------------------------------------------------------------------------

ConfigureRunner = Callable[..., None]


def run_configure(
    user_config: UserConfig,
    only: Sequence[str] = (),
    *,
    dry_run: bool = False,
    replace_key: bool = False,
    runner: ConfigureRunner | None = None,
    ask: Callable[[str], bool] | None = None,
    say: Say = print,
) -> int:
    """`rk fleet configure`: `rk configure` in each repo, which is asked about first.

    Configure writes to GitHub (labels, variables, key secrets), so each repo needs a typed yes;
    with no terminal the answer is no. `--dry-run` writes nothing, so it asks nothing.
    """
    runner = runner or bootstrap.run_configure
    ask = ask or bootstrap.ask

    def one(slug: str, path: Path) -> RepoResult:
        root = locate(slug, path)
        if not dry_run and not ask(f"Configure {slug}?"):
            return RepoResult(slug, Status.SKIP, "not asked, or answered no")
        changed: list[str] = []
        try:
            runner(root, changed=changed, dry_run=dry_run, replace_key=replace_key)
        finally:
            for item in changed:
                say(f"  - {item}")
        detail = f"{len(changed)} change(s)" if changed else "nothing to change"
        return RepoResult(slug, Status.OK, detail + (" (dry run)" if dry_run else ""))

    return _finish(_each(select(user_config, only), one, say), say)


# --- upgrade -------------------------------------------------------------------------------


def pr_title(config: RepoConfig, version: str) -> str:
    """The repo's own PR-title rule decides the form: its `pr-title` check would fail otherwise."""
    if config.checks.pr_title == "conventional":
        return f"chore: move repo-kit to {version}"
    return f"Move repo-kit to {version}"


def pr_body(version: str, previous: str, changed: Sequence[str]) -> str:
    listing = "\n".join(f"- `{path}`" for path in changed) or "- (only the pin)"
    return (
        f"Moves the repo-kit pin from {previous} to {version} and re-renders the shared files, "
        f"as `rk upgrade` does.\n\nShared files that change:\n{listing}\n\n"
        "Release notes: https://github.com/GreenMachine582/repo-kit/releases. "
        "Run `rk github diff` too: a release may change the rulesets or repo settings.\n\n"
        "Opened by `rk fleet upgrade`."
    )


def upgrade_branch(version: str) -> str:
    return f"chore/upgrade-{version}"


def _upgrade_one(
    slug: str,
    path: Path,
    *,
    apply: bool,
    github_for: GitHubFor,
    templates: Path | None,
    resolve_sha: bootstrap.ShaResolver | None,
) -> RepoResult:
    main = locate(slug, path)
    owner, repo = git.parse_owner_repo(git.remote_url(main))
    engine = repo_kit.__version__
    if engine == files.UNRELEASED:
        raise RkError("this rk is an unreleased build: install a release to upgrade repos with")
    config = load_repo_config(main)
    base, branch = config.default_base, upgrade_branch(engine)

    git.fetch(main)
    github = github_for(owner, repo)
    open_prs = [pr for pr in github.prs_for_branch(branch) if pr.state == "open"]
    if open_prs:
        return RepoResult(slug, Status.PR, f"#{open_prs[0].number} is already open ({branch})")
    if git.remote_branch_exists(main, branch):
        raise RkError(
            f"origin/{branch} exists with no open PR: open the PR for it, or delete the branch"
        )

    # A throwaway worktree cut from the remote base: the checkout itself is never read or touched.
    with tempfile.TemporaryDirectory(prefix="rk-fleet-") as scratch:
        work = Path(scratch) / "wt"
        git.run(["worktree", "add", "--detach", str(work), f"origin/{base}"], main)
        try:
            return _upgrade_in(
                slug, main, work, owner, repo, github, apply=apply, templates=templates,
                resolve_sha=resolve_sha,
                base=base, branch=branch,
            )  # fmt: skip
        finally:
            git.worktree_remove(main, work, force=True)
            if git.local_branch_exists(main, branch):
                git.branch_delete(main, branch, force=True)  # the branch rk just made


def _upgrade_in(
    slug: str,
    main: Path,
    work: Path,
    owner: str,
    repo: str,
    github: GitHub,
    *,
    apply: bool,
    templates: Path | None,
    base: str,
    branch: str,
    resolve_sha: bootstrap.ShaResolver | None = None,
) -> RepoResult:
    config = load_repo_config(work)
    if config.version == repo_kit.__version__:
        return RepoResult(slug, Status.OK, f"already at {config.version} on {base}")
    changed: list[str] = []
    result = bootstrap.upgrade(
        work, owner, repo, None, changed=changed, say=lambda _msg: None, templates=templates,
        dry_run=not apply, resolve_sha=resolve_sha,
    )  # fmt: skip
    summary = f"{result.current} -> {result.target}, {len(result.files)} shared file(s)"
    if not apply:
        return RepoResult(slug, Status.PLAN, summary)

    git.run(["switch", "-c", branch], work)
    # What CI's `managed-files` will run: the new pin and the new files must agree.
    code = files.run_sync(work, check=True, changed=[], say=lambda _msg: None, root=templates)
    if code != 0:
        raise RkError("the files written don't pass `rk sync --check`: nothing was pushed")
    title = pr_title(config, result.target)
    commit_message = f"chore: move repo-kit to {result.target}"
    problem = checks.check_header(commit_message, config.checks.commit_header_max)
    if problem:
        raise RkError(f"the commit message {commit_message!r} breaks the rules: {problem[0]}")
    git.run(["add", "-A"], work)
    git.run(["commit", "-q", "-m", commit_message], work)
    pushed = git.push(work, branch)
    if not pushed.ok:
        raise RkError(f"pushing {branch} was refused: {pushed.detail}")
    pr = github.create_pr(
        branch, base, title, pr_body(result.target, result.current, result.files), draft=False
    )
    return RepoResult(slug, Status.PR, f"#{pr.number} opened: {summary}")


def run_upgrade(
    user_config: UserConfig,
    only: Sequence[str] = (),
    *,
    apply: bool = False,
    github_for: GitHubFor = _github_for,
    templates: Path | None = None,
    resolve_sha: bootstrap.ShaResolver | None = None,
    say: Say = print,
) -> int:
    """`rk fleet upgrade`: move every repo's pin to this rk's release, one PR each.

    A dry run by default: it plans each upgrade in a scratch worktree and writes nothing.
    `--apply` pushes `chore/upgrade-<version>` and opens the PR (ready, to the repo's base) with the
    same files `rk upgrade` writes. A repo with an open upgrade PR, or already at this release, is
    left alone, so a re-run only finishes what's left.
    """

    def one(slug: str, path: Path) -> RepoResult:
        return _upgrade_one(
            slug, path, apply=apply, github_for=github_for, templates=templates,
            resolve_sha=resolve_sha,
        )  # fmt: skip

    results = _each(select(user_config, only), one)
    code = _finish(results, say)
    if not apply and any(r.status is Status.PLAN for r in results):
        say("Dry run: nothing was written. Re-run with --apply to open these PRs.")
    return code


# --- list ----------------------------------------------------------------------------------


def run_list(user_config: UserConfig, say: Say = print) -> int:
    """`rk fleet list`: the repos in `[fleet.repos]` and whether each path is its checkout."""

    def one(slug: str, path: Path) -> RepoResult:
        locate(slug, path)
        return RepoResult(slug, Status.OK, str(path))

    return _finish(_each(select(user_config, ()), one), say)
