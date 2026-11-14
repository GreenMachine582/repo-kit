"""Commit message and PR title rules, and the `rk check …` commands.

The rules live here once, for the `pr-checks` workflow and the local `commit-msg` hook alike.
See docs/checks-and-release.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from repo_kit import git
from repo_kit.config import load_repo_config

# The Conventional Commits types this toolkit accepts, in commits and in branch names.
TYPES = (
    "feat",
    "fix",
    "perf",
    "build",
    "docs",
    "refactor",
    "test",
    "ci",
    "chore",
    "style",
    "revert",
)

_HEADER = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\r\n]+)\))?(?P<bang>!)?(?P<colon>:)?(?P<space> ?)"
    r"(?P<subject>.*)$"
)
# release-please's own PR, and the `dev` → `main` PR: both allowed whatever the title rule.
_RELEASE_TITLE = re.compile(r"^(?:release: \S|chore\(main\): release \S)")
_SCISSORS = "# ------------------------ >8 ------------------------"


@dataclass(frozen=True)
class Header:
    type: str
    scope: str | None
    breaking: bool
    subject: str


def parse_header(line: str, *, ignore_case: bool = False) -> Header | None:
    """The parts of a conventional header, or None if `line` isn't one."""
    match = _HEADER.match(line)
    if not match or not match["colon"] or not match["space"] or not match["subject"].strip():
        return None
    kind = match["type"].lower() if ignore_case else match["type"]
    if kind not in TYPES or match["subject"] != match["subject"].lstrip():
        return None
    return Header(kind, match["scope"], bool(match["bang"]), match["subject"])


def _why_not(line: str) -> str:
    """Why `line` isn't a conventional header, as precisely as can be told."""
    match = _HEADER.match(line)
    if not line.strip():
        return "the first line is empty"
    if not match or not match["colon"]:
        return "not a conventional commit: start with `<type>: `, such as `feat: …`"
    if match["type"] not in TYPES:
        if match["type"].lower() in TYPES:
            return f"the type must be lower case: `{match['type'].lower()}`"
        return f"unknown type `{match['type']}`"
    if not match["space"]:
        return "put a space after the `:`"
    if match["subject"].strip() and match["subject"] != match["subject"].lstrip():
        return "use exactly one space after the `:`"
    return "the description after `: ` is empty"


def check_header(line: str, max_len: int) -> list[str]:
    """What's wrong with a commit's first line: nothing, if it's a conventional header that fits."""
    problems = [] if parse_header(line) else [_why_not(line)]
    if len(line) > max_len:
        problems.append(f"the first line is {len(line)} characters, over {max_len}")
    return problems


def header_of(message: str) -> str:
    """The first line of a commit message as git will store it: comments and the part below the
    scissors line (from `commit -v`) are dropped, as are leading blank lines."""
    for line in message.replace("\r\n", "\n").split("\n"):
        if line == _SCISSORS:
            break
        if line.startswith("#") or not line.strip():
            continue
        return line.rstrip()
    return ""


def check_pr_title(title: str, rule: str) -> str | None:
    """Why the PR title breaks `rule` (`[checks].pr_title`), or None if it's fine."""
    if _RELEASE_TITLE.match(title):
        return None
    if rule == "not-conventional":
        # Case-insensitive: `Feat: x` would still read as a change to release-please.
        if parse_header(title, ignore_case=True) is not None:
            return (
                "the PR title is a conventional commit. The merge commit carries the title, and "
                "release-please would count that change twice: write a plain sentence instead, "
                "such as `Add the folder picker`"
            )
        return None
    if parse_header(title) is None:
        return f"the PR title must be a conventional commit: {_why_not(title)}"
    return None


def types_hint() -> str:
    return f"Types: {', '.join(TYPES)}. See CONTRIBUTING.md."


# --- the commands --------------------------------------------------------------------------


def run_commits(cwd: Path, revisions: str, *, say: Callable[[str], None] = print) -> int:
    """`rk check commits <range>`: every non-merge commit in the range. Returns the exit code."""
    repo_root = git.toplevel(cwd)
    max_len = load_repo_config(repo_root).checks.commit_header_max
    commits = git.log_subjects(repo_root, revisions)
    bad = 0
    for sha, subject in commits:
        for problem in check_header(subject, max_len):
            say(f"{sha} {problem}: {subject}")
            bad += 1
    if bad:
        say(f"{bad} problem(s) in {len(commits)} commit(s). Reword those commits. {types_hint()}")
        return 1
    say(f"{len(commits)} commit(s) OK")
    return 0


def run_commit_msg(cwd: Path, message_file: Path, *, say: Callable[[str], None] = print) -> int:
    """`rk check commit-msg <file>`, as the `commit-msg` hook runs it. Returns the exit code."""
    repo_root = git.toplevel(cwd)
    if git.merge_in_progress(repo_root):
        return 0  # CI skips merge commits too
    max_len = load_repo_config(repo_root).checks.commit_header_max
    path = message_file if message_file.is_absolute() else cwd / message_file
    header = header_of(path.read_text(encoding="utf-8", errors="replace"))
    problems = check_header(header, max_len)
    if not problems:
        return 0
    for problem in problems:
        say(f"commit-msg: {problem}: {header}")
    say(types_hint())
    return 1


def run_pr_title(cwd: Path, title: str, *, say: Callable[[str], None] = print) -> int:
    """`rk check pr-title <title>`, by `[checks].pr_title`. Returns the exit code."""
    rule = load_repo_config(git.toplevel(cwd)).checks.pr_title
    problem = check_pr_title(title, rule)
    if problem:
        say(problem)
        return 1
    say(f"PR title OK ({rule})")
    return 0
