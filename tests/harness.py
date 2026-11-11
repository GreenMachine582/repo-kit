"""Git helpers for scenario tests: a bare origin and clones, all inside `tmp_path`."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}:\n{proc.stderr}{proc.stdout}")
    return proc.stdout.strip()


def commit(cwd: Path, name: str, content: str | None = None, message: str | None = None) -> str:
    """Write `name` and commit it; returns the new commit's SHA."""
    path = cwd / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else f"{name}\n", encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message or f"chore: add {name}")
    return git(cwd, "rev-parse", "HEAD")


def remote_sha(cwd: Path, branch: str) -> str | None:
    out = git(cwd, "ls-remote", "origin", f"refs/heads/{branch}")
    return out.split()[0] if out else None


def contains(cwd: Path, ancestor: str, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, ref], cwd=cwd, capture_output=True
    )
    return proc.returncode == 0
