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
        if action == "list":
            return worktree.list_worktrees(ctx)  # read-only: no `Changed:` list
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


def _secrets(action: str) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        from repo_kit import credentials

        if action == "show":
            return credentials.run_show()
        changed: list[str] = []
        code = 0
        try:
            if action == "init":
                code = credentials.run_init(file=args.file, changed=changed)
            elif action == "set-app":
                credentials.run_set_app(
                    args.app, client_id=args.client_id, pem=Path(args.pem), changed=changed
                )
            else:
                credentials.run_add_recipient(args.recipient, changed=changed)
        finally:
            _print_changes(changed)
        return code

    return handler


def _install(args: argparse.Namespace) -> int:
    from repo_kit import bootstrap

    changed: list[str] = []
    try:
        bootstrap.run_install(Path.cwd(), profile=args.profile, changed=changed, hooks=args.hooks)
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


def _check(kind: str) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        from repo_kit import checks

        if kind == "commits":
            return checks.run_commits(Path.cwd(), args.range)
        if kind == "commit-msg":
            return checks.run_commit_msg(Path.cwd(), Path(args.file))
        return checks.run_pr_title(Path.cwd(), args.title)

    return handler


def _github(apply_changes: bool) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        from repo_kit import github_config

        if not apply_changes:
            return github_config.run_github(Path.cwd(), apply_changes=False)
        changed: list[str] = []
        code = github_config.run_github(Path.cwd(), apply_changes=True, changed=changed)
        _print_changes(changed)
        return code

    return handler


def _prune(args: argparse.Namespace) -> int:
    from repo_kit import branches

    changed: list[str] = []
    code = branches.run_prune(Path.cwd(), apply=args.apply, changed=changed)
    if args.apply:
        _print_changes(changed)
    return code


def _doctor(args: argparse.Namespace) -> int:
    from repo_kit import doctor

    return doctor.run_doctor(Path.cwd(), as_json=args.json)


def _fleet(action: str) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        from repo_kit import fleet
        from repo_kit.config import load_user_config

        user = load_user_config()
        if action == "list":
            return fleet.run_list(user)
        if action == "doctor":
            return fleet.run_doctor(user, args.repo)
        if action == "sync":
            return fleet.run_sync_check(user, args.repo)
        if action == "configure":
            return fleet.run_configure(
                user, args.repo, dry_run=args.dry_run, replace_key=args.replace_key
            )
        return fleet.run_upgrade(user, args.repo, apply=args.apply)

    return handler


def _add_fleet(sub: SubParsers) -> None:
    fleet = sub.add_parser(
        "fleet", help="doctor, sync --check, configure and upgrade across every adopting repo"
    )
    fleet_sub = fleet.add_subparsers(dest="fleet_command", required=True, metavar="<command>")
    only = argparse.ArgumentParser(add_help=False)
    only.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="OWNER/NAME",
        help="only this repo from [fleet.repos] (repeatable)",
    )

    fleet_sub.add_parser(
        "list", help="the repos in [fleet.repos] and whether each path is its checkout"
    ).set_defaults(handler=_fleet("list"))
    fleet_sub.add_parser(
        "doctor", parents=[only], help="rk doctor in each repo (read-only)"
    ).set_defaults(handler=_fleet("doctor"))
    sync = fleet_sub.add_parser(
        "sync", parents=[only], help="rk sync --check in each repo (read-only)"
    )
    sync.add_argument(
        "--check", action="store_true", required=True, help="the only mode: write nothing"
    )
    sync.set_defaults(handler=_fleet("sync"))
    configure = fleet_sub.add_parser(
        "configure", parents=[only], help="rk configure in each repo, asking before each one"
    )
    configure.add_argument("--dry-run", action="store_true", help="report what would change")
    configure.add_argument(
        "--replace-key", action="store_true", help="overwrite the private key secrets if set"
    )
    configure.set_defaults(handler=_fleet("configure"))
    upgrade = fleet_sub.add_parser(
        "upgrade", parents=[only], help="one rk upgrade PR per repo (a dry run without --apply)"
    )
    upgrade.add_argument("--apply", action="store_true", help="push the branches and open the PRs")
    upgrade.set_defaults(handler=_fleet("upgrade"))


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

    listing = wt_sub.add_parser(
        "list", help="list this repo's worktrees, their PRs and any unsaved work (read-only)"
    )
    listing.set_defaults(handler=_wt_handler("list"))


def _add_pr_side(sub: SubParsers) -> None:
    update = sub.add_parser("update-prs", help="merge each ready PR's base into it (run by CI)")
    update.add_argument("--dry-run", action="store_true", help="report what would happen")
    update.set_defaults(handler=_update_prs)

    check = sub.add_parser("check", help="commit and PR title rules (used by pr-checks)")
    check_sub = check.add_subparsers(dest="check_command", required=True, metavar="<command>")
    commits = check_sub.add_parser("commits", help="check every non-merge commit in a range")
    commits.add_argument("range", help="revision range, such as origin/dev..HEAD")
    commits.set_defaults(handler=_check("commits"))
    msg = check_sub.add_parser("commit-msg", help="check one message file (commit-msg hook)")
    msg.add_argument("file")
    msg.set_defaults(handler=_check("commit-msg"))
    title = check_sub.add_parser("pr-title", help="check a PR title against [checks].pr_title")
    title.add_argument("title")
    title.set_defaults(handler=_check("pr-title"))


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

    github = sub.add_parser("github", help="rulesets and repo settings (`diff` by default)")
    github.set_defaults(handler=_github(False))
    gh_sub = github.add_subparsers(dest="github_command", metavar="<command>")
    gh_sub.add_parser("diff", help="live GitHub state vs config (read-only)").set_defaults(
        handler=_github(False)
    )
    gh_sub.add_parser("apply", help="apply after showing the diff and asking").set_defaults(
        handler=_github(True)
    )

    branches = sub.add_parser("branches", help="branch housekeeping")
    br_sub = branches.add_subparsers(dest="branches_command", required=True, metavar="<command>")
    prune = br_sub.add_parser("prune", help="list (or delete) branches whose PR is merged")
    prune.add_argument("--apply", action="store_true", help="delete instead of listing")
    prune.set_defaults(handler=_prune)

    install = sub.add_parser(
        "install", help="seed .repo-kit.toml, sync, add the plugin, create the base branch"
    )
    install.add_argument("--profile", default="dev-main", choices=["dev-main"])
    install.add_argument("--hooks", action="store_true", help="also install the commit-msg hook")
    install.set_defaults(handler=_install)

    configure = sub.add_parser(
        "configure",
        help="enrol this repo for gth-pr-bot and gth-release-bot: labels, Client IDs and keys",
    )
    configure.add_argument("--dry-run", action="store_true", help="report what would change")
    configure.add_argument(
        "--replace-key", action="store_true", help="overwrite the private key secrets if set"
    )
    configure.set_defaults(handler=_configure)

    secrets = sub.add_parser("secrets", help="the Apps' credentials, in a sops + age file")
    sec_sub = secrets.add_subparsers(dest="secrets_command", required=True, metavar="<command>")
    init = sec_sub.add_parser(
        "init",
        help="set this machine up: age key, user config entry, credentials file (safe to re-run)",
    )
    init.add_argument(
        "--file",
        metavar="PATH",
        help="where the shared credentials file goes (default: ~/OneDrive/repo-kit/"
        "secrets.sops.json, or the home folder without OneDrive)",
    )
    init.set_defaults(handler=_secrets("init"))
    set_app = sec_sub.add_parser("set-app", help="store an App's Client ID and private key")
    set_app.add_argument("app", choices=["pr-bot", "release-bot"])
    set_app.add_argument("--client-id", required=True, help="the App's Client ID (Iv23…)")
    set_app.add_argument("--pem", required=True, metavar="PATH", help="the App's downloaded .pem")
    set_app.set_defaults(handler=_secrets("set-app"))
    add = sec_sub.add_parser(
        "add-recipient", help="let another machine's age key decrypt the credentials file"
    )
    add.add_argument("recipient", metavar="AGE_PUBLIC_KEY", help="age1…, from `rk secrets init`")
    add.set_defaults(handler=_secrets("add-recipient"))
    sec_sub.add_parser(
        "show", help="list each App's Client ID and whether its key is present (never the key)"
    ).set_defaults(handler=_secrets("show"))

    bootstrap = sub.add_parser("bootstrap", help="install + configure + doctor")
    bootstrap.add_argument("--profile", default="dev-main", choices=["dev-main"])
    bootstrap.set_defaults(handler=_bootstrap)

    upgrade = sub.add_parser(
        "upgrade", help="move the version pin to this rk's release and re-render"
    )
    upgrade.add_argument(
        "version", nargs="?", help="vX.Y.Z, which must be this rk's release; defaults to it"
    )
    upgrade.set_defaults(handler=_upgrade)

    doctor = sub.add_parser("doctor", help="read-only health check")
    doctor.add_argument("--json", action="store_true", help="print one JSON document, nothing else")
    doctor.set_defaults(handler=_doctor)


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
    _add_fleet(sub)
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
