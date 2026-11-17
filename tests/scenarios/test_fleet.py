"""`rk fleet` scenarios: fake repos (a bare origin and a clone each) in `tmp_path`, a fake PR
store, and test templates. Nothing touches a real repo or GitHub."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FakeGitHub
from harness import commit, git

import repo_kit
from repo_kit import fleet
from repo_kit.config import FleetConfig, UserConfig
from repo_kit.errors import RkError
from repo_kit.files import MANIFEST_NAME, run_sync

MANIFEST = '[files."ci/caller.yml"]\nmode = "managed"\n'
TOML = 'version = "1.0.0"\n\n[project]\nname = "x"\n'


@pytest.fixture
def templates(tmp_path):
    root = tmp_path / "templates"
    common = root / "common"
    (common / "ci").mkdir(parents=True)
    (common / MANIFEST_NAME).write_text(MANIFEST, encoding="utf-8")
    (common / "ci/caller.yml").write_text("uses: repo-kit@v${version}\n", encoding="utf-8")
    return root


@pytest.fixture
def make_repo(tmp_path, git_env, templates, monkeypatch):
    """A repo named `name`: a bare origin with `main` and `dev`, and a clone synced at 1.0.0 whose
    state is pushed to `dev`. The engine ends at 1.1.0, so every repo is one release behind."""

    def make(name: str) -> Path:
        bare = tmp_path / f"{name}.git"
        git(tmp_path, "init", "-q", "--bare", str(bare))
        work = tmp_path / "checkouts" / name
        work.parent.mkdir(exist_ok=True)
        git(tmp_path, "clone", "-q", str(bare), str(work))
        commit(work, "README.md", "# demo\n", "chore: initial commit")
        (work / ".repo-kit.toml").write_text(TOML, encoding="utf-8")
        monkeypatch.setattr(repo_kit, "__version__", "1.0.0")
        run_sync(work, check=False, changed=[], say=lambda _: None, root=templates)
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "chore: adopt repo-kit")
        git(work, "push", "-q", "origin", "main", "main:dev")
        monkeypatch.setattr(repo_kit, "__version__", "1.1.0")
        return work

    return make


def user_config(**repos: Path) -> UserConfig:
    return UserConfig(fleet=FleetConfig({f"local/{name}": path for name, path in repos.items()}))


def upgrade(config, templates, github, only=(), apply=False):
    said: list[str] = []
    code = fleet.run_upgrade(
        config,
        only,
        apply=apply,
        github_for=lambda _o, _r: github,
        templates=templates,
        say=said.append,
    )
    return code, "\n".join(said)


def remote_file(origin: Path, ref: str, path: str) -> str:
    return git(origin, "show", f"{ref}:{path}")


def worktree_count(work: Path) -> int:
    return git(work, "worktree", "list").count("\n") + 1


def test_a_dry_run_plans_each_repo_and_writes_nothing(make_repo, templates):
    a, b = make_repo("a"), make_repo("b")
    github = FakeGitHub()
    code, said = upgrade(user_config(a=a, b=b), templates, github)
    assert code == 0
    assert said.count("PLAN") == 2
    assert "1.0.0 -> 1.1.0, 1 shared file(s)" in said
    assert "Dry run: nothing was written" in said
    assert github.created == []
    for work in (a, b):
        assert git(work, "branch", "-r", "--list", "origin/chore/*") == ""
        assert 'version = "1.0.0"' in (work / ".repo-kit.toml").read_text(encoding="utf-8")
        assert worktree_count(work) == 1


def test_apply_pushes_a_branch_and_opens_one_pr_per_repo(make_repo, templates, tmp_path):
    a = make_repo("a")
    github = FakeGitHub()
    (a / "scratch.txt").write_text("my work\n", encoding="utf-8")  # a busy checkout is no obstacle
    code, said = upgrade(user_config(a=a), templates, github, apply=True)
    assert code == 0, said
    assert "#1 opened: 1.0.0 -> 1.1.0" in said

    bare, branch = tmp_path / "a.git", "chore/upgrade-1.1.0"
    assert 'version = "1.1.0"' in remote_file(bare, branch, ".repo-kit.toml")
    assert remote_file(bare, branch, "ci/caller.yml") == "uses: repo-kit@v1.1.0"
    assert json.loads(remote_file(bare, branch, ".repo-kit.lock"))["version"] == "1.1.0"
    assert git(bare, "log", "-1", "--format=%s", branch) == "chore: move repo-kit to 1.1.0"
    [(number, title, body)] = github.created
    assert (number, title) == (1, "Move repo-kit to 1.1.0")
    assert "`ci/caller.yml`" in body
    pr = github.get(1)
    assert (pr.head_ref, pr.base_ref, pr.draft) == (branch, "dev", False)

    # The checkout's files and branches are as they were; the scratch worktree is gone.
    assert (a / "scratch.txt").read_text(encoding="utf-8") == "my work\n"
    assert 'version = "1.0.0"' in (a / ".repo-kit.toml").read_text(encoding="utf-8")
    assert git(a, "branch", "--list", "chore/*") == ""
    assert worktree_count(a) == 1


def test_a_re_run_leaves_an_open_pr_alone(make_repo, templates):
    a = make_repo("a")
    github = FakeGitHub()
    upgrade(user_config(a=a), templates, github, apply=True)
    code, said = upgrade(user_config(a=a), templates, github, apply=True)
    assert code == 0
    assert "#1 is already open" in said
    assert len(github.created) == 1


def test_a_pushed_branch_without_a_pr_is_a_failure_not_a_force(make_repo, templates):
    a = make_repo("a")
    git(a, "push", "-q", "origin", "main:chore/upgrade-1.1.0")
    code, said = upgrade(user_config(a=a), templates, FakeGitHub(), apply=True)
    assert code == 1
    assert "exists with no open PR" in said


def test_a_repo_already_at_the_release_is_ok(make_repo, templates, monkeypatch):
    a = make_repo("a")
    monkeypatch.setattr(repo_kit, "__version__", "1.0.0")
    code, said = upgrade(user_config(a=a), templates, FakeGitHub(), apply=True)
    assert code == 0
    assert "already at 1.0.0 on dev" in said


def test_one_failing_repo_does_not_stop_the_others(make_repo, templates, tmp_path):
    a, b = make_repo("a"), make_repo("b")
    # A hand edit on `a`'s dev: the upgrade refuses it (rule 4), and `b` goes ahead.
    (a / "ci/caller.yml").write_text("edited\n", encoding="utf-8")
    git(a, "commit", "-qam", "chore: hand edit")
    git(a, "push", "-q", "origin", "main:dev")
    github = FakeGitHub()
    code, said = upgrade(user_config(a=a, b=b), templates, github, apply=True)
    assert code == 1
    rows = {line.split()[0]: line for line in said.splitlines() if line.startswith("local/")}
    assert "FAIL" in rows["local/a"]
    assert "refused" in said
    assert "PR" in rows["local/b"]
    assert git(tmp_path / "a.git", "branch", "--list", "chore/*") == ""
    assert len(github.created) == 1


def test_the_prs_own_title_rule_picks_its_form(make_repo, templates):
    a = make_repo("a")
    (a / ".repo-kit.toml").write_text(TOML + '\n[checks]\npr_title = "conventional"\n')
    git(a, "commit", "-qam", "chore: conventional titles")
    git(a, "push", "-q", "origin", "main:dev")
    github = FakeGitHub()
    upgrade(user_config(a=a), templates, github, apply=True)
    assert github.created[0][1] == "chore: move repo-kit to 1.1.0"


def test_only_limits_the_repos_and_unknown_ones_are_refused(make_repo, templates):
    a, b = make_repo("a"), make_repo("b")
    config = user_config(a=a, b=b)
    code, said = upgrade(config, templates, FakeGitHub(), only=["LOCAL/B"])
    assert code == 0
    assert "local/b" in said
    assert "local/a" not in said
    with pytest.raises(RkError, match=r"not in \[fleet.repos\]: local/zzz"):
        upgrade(config, templates, FakeGitHub(), only=["local/zzz"])


def test_a_path_that_is_another_repo_is_refused(make_repo, templates):
    make_repo("a")
    b = make_repo("b")
    code, said = upgrade(user_config(a=b), templates, FakeGitHub())
    assert code == 1
    assert "origin is local/b, not local/a" in said


def test_a_missing_path_is_a_failed_row(templates, tmp_path):
    code, said = upgrade(user_config(a=tmp_path / "gone"), templates, FakeGitHub())
    assert code == 1
    assert "isn't a folder" in said


def test_no_repos_configured_says_how_to_add_them(templates):
    with pytest.raises(RkError, match=r"\[fleet.repos\]"):
        upgrade(UserConfig(), templates, FakeGitHub())


def test_sync_check_lists_what_drifted_and_a_trailing_pin(make_repo, templates, monkeypatch):
    a, b = make_repo("a"), make_repo("b")
    monkeypatch.setattr(repo_kit, "__version__", "1.0.0")
    (b / "ci/caller.yml").write_text("edited\n", encoding="utf-8")
    said: list[str] = []
    code = fleet.run_sync_check(user_config(a=a, b=b), templates=templates, say=said.append)
    out = "\n".join(said)
    assert code == 1
    assert "local/a  OK     in sync" in out
    assert "out of sync: ci/caller.yml (refused)" in out

    monkeypatch.setattr(repo_kit, "__version__", "1.1.0")
    said.clear()
    code = fleet.run_sync_check(user_config(a=a), templates=templates, say=said.append)
    assert code == 1
    assert "pins repo-kit 1.0.0, but this rk is 1.1.0" in "\n".join(said)
    # Read-only: nothing was written.
    assert (b / "ci/caller.yml").read_text(encoding="utf-8") == "edited\n"


def doctor_doc(*checks: tuple[str, str]) -> str:
    rows = [{"name": name, "status": status, "detail": ""} for name, status in checks]
    return json.dumps({"ok": all(s != "FAIL" for _, s in checks), "checks": rows})


def test_doctor_summarises_each_repos_json_and_fails_on_a_failing_check(make_repo):
    a, b, c = make_repo("a"), make_repo("b"), make_repo("c")
    docs = {
        "a": doctor_doc(("tools", "OK"), ("worktrees", "SKIP")),
        "b": doctor_doc(("tools", "OK"), ("plugin", "WARN")),
        "c": doctor_doc(("tools", "OK"), ("managed files", "FAIL"), ("plugin", "WARN")),
    }
    seen: list[Path] = []

    def runner(path: Path) -> tuple[int, str]:
        seen.append(path)
        return 0, docs[path.name]

    said: list[str] = []
    code = fleet.run_doctor(user_config(a=a, b=b, c=c), runner=runner, say=said.append)
    out = "\n".join(said)
    assert code == 1
    assert seen == [a, b, c]
    assert "local/a  OK     2 check(s)" in out
    assert "local/b  WARN   2 check(s); WARN: plugin" in out
    assert "FAIL: managed files; WARN: plugin" in out
    assert out.splitlines()[-1] == "3 repo(s): 1 ok, 1 warn, 1 fail"


def test_doctor_output_that_isnt_the_document_is_a_failed_row(make_repo):
    a = make_repo("a")
    said: list[str] = []
    code = fleet.run_doctor(user_config(a=a), runner=lambda _p: (0, "not json"), say=said.append)
    assert code == 1
    assert "wasn't the document expected" in "\n".join(said)


def test_configure_asks_for_each_repo_and_skips_a_no(make_repo):
    a, b = make_repo("a"), make_repo("b")
    asked: list[str] = []
    ran: list[tuple[Path, bool, bool]] = []

    def ask(question: str) -> bool:
        asked.append(question)
        return "local/a" in question

    def runner(path: Path, *, changed: list[str], dry_run: bool, replace_key: bool) -> None:
        ran.append((path, dry_run, replace_key))
        changed.append("created label wip")

    said: list[str] = []
    code = fleet.run_configure(
        user_config(a=a, b=b), replace_key=True, runner=runner, ask=ask, say=said.append
    )
    assert code == 0
    assert asked == ["Configure local/a?", "Configure local/b?"]
    assert ran == [(a, False, True)]
    out = "\n".join(said)
    assert "  - created label wip" in out
    assert "local/b  SKIP" in out


def test_configure_dry_run_asks_nothing(make_repo):
    a = make_repo("a")
    ran: list[bool] = []

    def runner(path: Path, *, changed: list[str], dry_run: bool, replace_key: bool) -> None:
        ran.append(dry_run)

    def ask(question: str) -> bool:
        raise AssertionError("a dry run must not ask")

    code = fleet.run_configure(
        user_config(a=a), dry_run=True, runner=runner, ask=ask, say=lambda _: None
    )
    assert code == 0
    assert ran == [True]


def test_configure_with_no_terminal_changes_nothing(make_repo, monkeypatch):
    a = make_repo("a")
    monkeypatch.setattr("sys.stdin", type("NoTty", (), {"isatty": lambda self: False})())
    ran: list[Path] = []
    code = fleet.run_configure(
        user_config(a=a), runner=lambda p, **_k: ran.append(p), say=lambda _: None
    )
    assert code == 0
    assert ran == []
