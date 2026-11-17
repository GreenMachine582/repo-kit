"""Shared fixtures. Tests only ever touch `tmp_path`, never a real repo or GitHub.

The scenario harness: a bare `origin` with `main` and `dev`, clones of it, a worktree root, and a
fake PR store. Git runs with an isolated global config, so the developer's settings and CI's lack
of an identity don't matter.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from fakes import FakeGitHub
from harness import commit, git

from repo_kit.config import UserConfig
from repo_kit.worktree import Context


def _isolate_git(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gitconfig = home / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n"
        "[init]\n\tdefaultBranch = main\n"
        "[core]\n\tautocrlf = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("REPO_KIT_CONFIG", str(home / "no-user-config.toml"))


@pytest.fixture
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_git(tmp_path, monkeypatch)


@pytest.fixture(scope="session")
def _origin_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the bare origin once per test process; each test gets its own copy.

    Starting git is slow on Windows, and every scenario test needs this origin, so copying a
    finished one saves several git runs per test.
    """
    root = tmp_path_factory.mktemp("origin-template")
    bare = root / "origin.git"
    with pytest.MonkeyPatch.context() as mp:
        _isolate_git(root, mp)
        git(root, "init", "-q", "--bare", str(bare))
        seed = root / "seed"
        git(root, "clone", "-q", str(bare), str(seed))
        commit(seed, "README.md", "# demo\n", "chore: initial commit")
        git(seed, "push", "-q", "origin", "main")
        git(seed, "push", "-q", "origin", "main:dev")
    return bare


@pytest.fixture
def origin(tmp_path: Path, git_env: None, _origin_template: Path) -> Path:
    """A bare origin with one commit on `main` and `dev`."""
    bare = tmp_path / "origin.git"
    shutil.copytree(_origin_template, bare)
    return bare


@pytest.fixture
def clone(tmp_path: Path, origin: Path) -> Callable[[str], Path]:
    def make(name: str) -> Path:
        path = tmp_path / name
        git(tmp_path, "clone", "-q", str(origin), str(path))
        return path

    return make


@pytest.fixture
def wt_root(tmp_path: Path) -> Path:
    return tmp_path / "wt"


@pytest.fixture
def github(origin: Path) -> FakeGitHub:
    return FakeGitHub(origin)


@pytest.fixture
def make_ctx(wt_root: Path, github: FakeGitHub) -> Callable[[Path], Context]:
    """A `Context` for running rk from `cwd`, with the fake PR store and `wt_root`."""

    def make(cwd: Path) -> Context:
        ctx = Context.load(cwd, github=github, user_config=UserConfig(worktree_root=wt_root))
        ctx.say = lambda _msg: None
        return ctx

    return make
