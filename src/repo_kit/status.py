"""`rk status`: one read-only summary of the current repo.

The worktrees and their PRs (the `rk wt list` data), doctor's result as a count plus each WARN
or FAIL by name, and the pinned version next to this rk's. It reuses `worktree.worktree_statuses`
and `doctor.run_checks` rather than redoing either, writes nothing and doesn't fetch. See
docs/worktrees.md and docs/adoption.md.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import repo_kit
from repo_kit import doctor, files, worktree
from repo_kit.doctor import Check, Status
from repo_kit.errors import RkError
from repo_kit.worktree import Context, WorktreeStatus

SCHEMA_VERSION = 1  # of the `--json` document; bump on a breaking change


@dataclass(frozen=True)
class Report:
    repo: str  # owner/name
    pin: str  # the version in .repo-kit.toml
    engine: str  # this rk's version
    worktrees: tuple[WorktreeStatus, ...]
    worktrees_note: str  # why the PRs are unknown, or ""
    checks: tuple[Check, ...]

    @property
    def pin_matches(self) -> bool:
        """An unreleased rk renders from its own templates, so it matches any pin."""
        return self.engine in (files.UNRELEASED, self.pin)

    @property
    def ok(self) -> bool:
        return not doctor.failed(list(self.checks))

    @property
    def counts(self) -> dict[str, int]:
        found = Counter(c.status for c in self.checks)
        return {str(status): found[status] for status in Status}

    @property
    def problems(self) -> list[Check]:
        return [c for c in self.checks if c.status in (Status.WARN, Status.FAIL)]


def build(ctx: Context, checks: Sequence[Check]) -> Report:
    """The report for `ctx`'s repo, given doctor's checks for it."""
    statuses, note = worktree.worktree_statuses(ctx)
    return Report(
        repo=f"{ctx.owner}/{ctx.repo}",
        pin=ctx.repo_config.version,
        engine=repo_kit.__version__,
        worktrees=tuple(statuses),
        worktrees_note=note,
        checks=tuple(checks),
    )


def render(report: Report) -> str:
    lines = [report.repo]
    if report.pin_matches:
        lines.append(f"Pin: {report.pin} (this rk {report.engine})")
    else:
        lines.append(
            f"Pin: {report.pin}, but this rk is {report.engine}: `rk upgrade` moves the pin"
        )
    lines.append("")
    lines.append("Worktrees:")
    if report.worktrees:
        rows = worktree.render_list(report.worktrees, prs_known=not report.worktrees_note)
        lines += [f"  {row}" for row in rows]
    else:
        lines.append("  none")
    lines += [f"  {line}" for line in report.worktrees_note.splitlines()]
    lines.append("")
    counts = ", ".join(f"{n} {status}" for status, n in report.counts.items())
    lines.append(f"Doctor: {len(report.checks)} checks: {counts}")
    for check in report.problems:
        lines.append(f"  [{check.status}] {check.label} ({check.id})")
    return "\n".join(lines)


def _worktree_dict(st: WorktreeStatus, prs_known: bool) -> dict[str, object]:
    pr = {"number": st.pr.number, "state": worktree.pr_state(st.pr)} if st.pr is not None else None
    return {
        "branch": st.branch,
        "path": str(st.path),
        "pr": pr,
        "pr_known": prs_known,
        "uncommitted": st.uncommitted,  # null: the directory is gone
        "unpushed": st.unpushed,
    }


def to_json(report: Report) -> str:
    """The same data as `render`, for tools: `ok` is False only if a doctor check FAILed."""
    known = not report.worktrees_note
    document = {
        "schema_version": SCHEMA_VERSION,
        "ok": report.ok,
        "repo": report.repo,
        "pin": {"version": report.pin, "rk": report.engine, "matches": report.pin_matches},
        "worktrees": [_worktree_dict(st, known) for st in report.worktrees],
        "worktrees_note": report.worktrees_note,
        "doctor": {
            "counts": report.counts,
            "problems": [doctor.check_dict(c) for c in report.problems],
        },
    }
    return json.dumps(document, indent=2, ensure_ascii=False)


def error_json(message: str) -> str:
    """The `--json` document when status can't start (no repo, bad config): nothing was read."""
    return json.dumps(
        {"schema_version": SCHEMA_VERSION, "ok": False, "error": message},
        indent=2,
        ensure_ascii=False,
    )


def run_status(cwd: Path, *, as_json: bool = False, say: Callable[[str], None] = print) -> int:
    """The command. Returns 1 if any doctor check FAILed, else 0. With `as_json`, `say` gets
    only the JSON document."""
    try:
        ctx = Context.load(cwd)
        report = build(ctx, doctor.run_checks(cwd))
    except RkError as exc:
        if not as_json:
            raise
        say(error_json(str(exc)))
        return 1
    say(to_json(report) if as_json else render(report))
    return 0 if report.ok else 1
