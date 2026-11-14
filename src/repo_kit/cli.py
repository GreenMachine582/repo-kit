"""The `rk` command line.

Every command is declared here with its final arguments, so `--help` already describes the whole
tool. Commands that aren't built yet print which phase delivers them (see TODO.md) and exit
with `NOT_IMPLEMENTED`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from repo_kit import __version__
from repo_kit.errors import RkError

NOT_IMPLEMENTED = 2

Handler = Callable[[argparse.Namespace], int]
if TYPE_CHECKING:
    # Subscriptable only for the type checker, not at runtime.
    SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]


def _not_implemented(command: str, phase: str) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        print(f"rk {command}: not implemented yet (Phase {phase})", file=sys.stderr)
        return NOT_IMPLEMENTED

    return handler


def _print_changes(changed: list[str]) -> None:
    print("Changed:" if changed else "Changed: nothing")
    for item in changed:
        print(f"  - {item}")


def _wt_handler(action: str) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        # Imported here so `rk --help` and the not-yet-built commands don't need git.
        from repo_kit import worktree

        ctx = worktree.Context.load(Path.cwd())
        try:
            if action == "start":
                worktree.start(ctx, args.branch, args.from_branch, args.link, args.no_setup)
            elif action == "sync":
                worktree.sync(ctx, args.branch)
            else:
                worktree.done(
                    ctx,
                    args.branch,
                    allow_unmerged=args.allow_unmerged,
                    discard_changes=args.discard_changes,
                    delete_unpushed=args.delete_unpushed,
                )
        finally:
            _print_changes(ctx.changed)
        return 0

    return handler


def _update_prs(args: argparse.Namespace) -> int:
    # Imported here so `rk --help` and the not-yet-built commands don't load it.
    from repo_kit import reconcile

    report = reconcile.run(Path.cwd(), dry_run=args.dry_run)
    _print_changes(report.changed)
    return 1 if report.failed else 0


def _sync(args: argparse.Namespace) -> int:
    if args.adopt:
        print(
            "rk sync --adopt: a Future item (see TODO.md). Plain `rk sync` takes over a file only "
            "if it already matches, and refuses with the diff otherwise.",
            file=sys.stderr,
        )
        return NOT_IMPLEMENTED
    from repo_kit import files

    changed: list[str] = []
    try:
        return files.run_sync(Path.cwd(), check=args.check, changed=changed)
    finally:
        if not args.check:
            _print_changes(changed)


def _configure(args: argparse.Namespace) -> int:
    from repo_kit import bootstrap

    changed: list[str] = []
    try:
        bootstrap.run_configure(
            Path.cwd(), changed=changed, dry_run=args.dry_run, replace_key=args.replace_key
        )
    finally:
        _print_changes(changed)
    return 0


def _install(args: argparse.Namespace) -> int:
    if args.hooks:
        # The hook runs `rk check commit-msg`: installed before that exists, it would block commits.
        print(
            "rk install --hooks: not implemented yet (Phase 1D), with the commit rules it runs",
            file=sys.stderr,
        )
        return NOT_IMPLEMENTED
    from repo_kit import bootstrap

    changed: list[str] = []
    try:
        bootstrap.run_install(Path.cwd(), profile=args.profile, changed=changed)
    finally:
        _print_changes(changed)
    return 0


def _upgrade(args: argparse.Namespace) -> int:
    from repo_kit import bootstrap

    changed: list[str] = []
    try:
        bootstrap.run_upgrade(Path.cwd(), args.version, changed=changed)
    finally:
        _print_changes(changed)
    return 0


def _bootstrap(args: argparse.Namespace) -> int:
    from repo_kit import bootstrap

    changed: list[str] = []
    try:
        bootstrap.run_bootstrap(Path.cwd(), profile=args.profile, changed=changed)
    finally:
        _print_changes(changed)
    return 0


def _add_worktree(sub: SubParsers) -> None:
    wt = sub.add_parser("wt", help="one branch = one worktree = one Claude session")
    wt_sub = wt.add_subparsers(dest="wt_command", required=True, metavar="<command>")

    start = wt_sub.add_parser("start", help="create a branch and its worktree, then set it up")
    start.add_argument("branch", help="branch name, as <type>/<description>")
    start.add_argument(
        "--from",
        dest="from_branch",
        metavar="BRANCH",
        help="branch off this instead of the profile's default base",
    )
    start.add_argument(
        "--link",
        action="append",
        default=[],
        metavar="PKG=BRANCH",
        help="use a local worktree of a GTH package (repeatable)",
    )
    start.add_argument("--no-setup", action="store_true", help="skip [worktree].setup")
    start.set_defaults(handler=_wt_handler("start"))

    sync = wt_sub.add_parser("sync", help="merge the remote branch and its PR base, check, push")
    sync.add_argument("branch", nargs="?", help="defaults to the current worktree's branch")
    sync.set_defaults(handler=_wt_handler("sync"))

    done = wt_sub.add_parser("done", help="remove a finished worktree and its branch")
    done.add_argument("branch", nargs="?", help="defaults to the current worktree's branch")
    done.add_argument(
        "--allow-unmerged", action="store_true", help="allow a PR that isn't merged or closed"
    )
    done.add_argument(
        "--discard-changes", action="store_true", help="allow uncommitted changes, which are lost"
    )
    done.add_argument(
        "--delete-unpushed", action="store_true", help="allow unpushed commits, which are lost"
    )
    done.set_defaults(handler=_wt_handler("done"))


def _add_pr_side(sub: SubParsers) -> None:
    update = sub.add_parser("update-prs", help="merge each ready PR's base into it (run by CI)")
    update.add_argument("--dry-run", action="store_true", help="report what would happen")
    update.set_defaults(handler=_update_prs)

    check = sub.add_parser("check", help="commit and PR title rules (used by pr-checks)")
    check_sub = check.add_subparsers(dest="check_command", required=True, metavar="<command>")
    commits = check_sub.add_parser("commits", help="check every non-merge commit in a range")
    commits.add_argument("range", help="revision range, such as origin/dev..HEAD")
    commits.set_defaults(handler=_not_implemented("check commits", "1D"))
    msg = check_sub.add_parser("commit-msg", help="check one message file (commit-msg hook)")
    msg.add_argument("file")
    msg.set_defaults(handler=_not_implemented("check commit-msg", "1D"))
    title = check_sub.add_parser("pr-title", help="check a PR title against [checks].pr_title")
    title.add_argument("title")
    title.set_defaults(handler=_not_implemented("check pr-title", "1D"))


def _add_setup_side(sub: SubParsers) -> None:
    sync = sub.add_parser("sync", help="render managed files and blocks into this repo")
    mode = sync.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="write nothing; fail if anything differs from the pinned version",
    )
    mode.add_argument(
        "--adopt",
        action="store_true",
        help="take over existing files after showing each difference",
    )
    sync.set_defaults(handler=_sync)

    github = sub.add_parser("github", help="rulesets and repo settings")
    gh_sub = github.add_subparsers(dest="github_command", required=True, metavar="<command>")
    gh_sub.add_parser("diff", help="live GitHub state vs config (read-only)").set_defaults(
        handler=_not_implemented("github diff", "1D")
    )
    gh_sub.add_parser("apply", help="apply after showing the diff and asking").set_defaults(
        handler=_not_implemented("github apply", "1D")
    )

    branches = sub.add_parser("branches", help="branch housekeeping")
    br_sub = branches.add_subparsers(dest="branches_command", required=True, metavar="<command>")
    prune = br_sub.add_parser("prune", help="list (or delete) branches whose PR is merged")
    prune.add_argument("--apply", action="store_true", help="delete instead of listing")
    prune.set_defaults(handler=_not_implemented("branches prune", "1D"))

    install = sub.add_parser(
        "install", help="seed .repo-kit.toml, sync, add the plugin, create the base branch"
    )
    install.add_argument("--profile", default="dev-main", choices=["dev-main"])
    install.add_argument("--hooks", action="store_true", help="also install the commit-msg hook")
    install.set_defaults(handler=_install)

    configure = sub.add_parser(
        "configure", help="enrol this repo for gth-pr-bot: labels, Client ID and key"
    )
    configure.add_argument("--dry-run", action="store_true", help="report what would change")
    configure.add_argument(
        "--replace-key", action="store_true", help="overwrite the private key secret if it's set"
    )
    configure.set_defaults(handler=_configure)

    bootstrap = sub.add_parser("bootstrap", help="install + configure (+ doctor, once it is built)")
    bootstrap.add_argument("--profile", default="dev-main", choices=["dev-main"])
    bootstrap.set_defaults(handler=_bootstrap)

    upgrade = sub.add_parser(
        "upgrade", help="move the version pin to this rk's release and re-render"
    )
    upgrade.add_argument(
        "version", nargs="?", help="vX.Y.Z, which must be this rk's release; defaults to it"
    )
    upgrade.set_defaults(handler=_upgrade)

    sub.add_parser("doctor", help="read-only health check").set_defaults(
        handler=_not_implemented("doctor", "1C")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rk",
        description="repo-kit: worktree sessions, PRs that stay current, shared repo setup.",
    )
    parser.add_argument("--version", action="version", version=f"rk {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    _add_worktree(sub)
    _add_pr_side(sub)
    _add_setup_side(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        handler: Handler = args.handler
        return handler(args)
    except RkError as exc:
        print(f"rk: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
