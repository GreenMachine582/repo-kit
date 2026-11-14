"""`rk install` and `rk bootstrap`: scenario 13 (docs/testing.md), with the real templates."""

from __future__ import annotations

import json

import pytest
from fakes import FakeRepoSetup
from harness import git, push_branch, remote_sha

import repo_kit
from repo_kit import bootstrap
from repo_kit.config import PrBotConfig, ProjectConfig, RepoConfig, UserConfig, load_repo_config
from repo_kit.errors import RkError
from repo_kit.files import LOCK_NAME, SyncError, collect_templates, templates_root

KEY = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"
CALLER = ".github/workflows/update-prs.yml"


@pytest.fixture(autouse=True)
def released_engine(monkeypatch):
    """Install pins the running rk, which must be a release."""
    monkeypatch.setattr(repo_kit, "__version__", "1.4.0")


@pytest.fixture
def only_main(origin):
    """An origin that has never had `dev`."""
    git(origin, "update-ref", "-d", "refs/heads/dev")


def install(work, github, answer=False):
    changed: list[str] = []
    said: list[str] = []
    asked: list[str] = []

    def confirm(question):
        asked.append(question)
        return answer

    bootstrap.install(
        work,
        "local",
        "demo",
        github,
        profile="dev-main",
        changed=changed,
        confirm=confirm,
        say=said.append,
    )
    return changed, "\n".join(said), asked


def read(work, path):
    return (work / path).read_text(encoding="utf-8")


def untouched(work):
    return git(work, "status", "--porcelain") == ""


def test_a_fresh_install_writes_the_config_the_files_and_the_lock(clone, github):
    work = clone("work")

    changed, _said, asked = install(work, github)

    assert load_repo_config(work) == RepoConfig(
        version="1.4.0", project=ProjectConfig(name="demo", release_type="simple")
    )
    expected = {t.path for t in collect_templates(templates_root(), RepoConfig())}
    assert ".repo-kit.toml" in expected
    assert set(json.loads(read(work, LOCK_NAME))["files"]) == expected
    assert all((work / path).is_file() for path in expected)
    assert "update-prs.yml@v1.4.0" in read(work, CALLER)
    settings = json.loads(read(work, ".claude/settings.json"))
    assert settings["extraKnownMarketplaces"]["repo-kit"]["source"] == {
        "source": "github",
        "repo": "GreenMachine582/repo-kit",
    }
    assert settings["enabledPlugins"] == {"repo-kit@repo-kit": True}
    # `dev` was already there, and there are no PRs: nothing to create or ask.
    assert asked == []
    assert not any("created `dev`" in line for line in changed)


BLOCKS = ("branches", "pull-requests", "chained-prs", "releasing")


def test_a_fresh_install_writes_the_contributing_blocks_in_order(clone, github):
    work = clone("work")

    install(work, github)

    text = read(work, "CONTRIBUTING.md")
    starts = [text.index(f"<!-- >>> repo-kit: {name} -->") for name in BLOCKS]
    assert starts == sorted(starts)
    assert text.startswith("<!-- >>> repo-kit: branches -->\n## 🔀 Branches\n")
    assert "at most\n100 characters" in text
    assert "PR titles are **not** Conventional Commits" in text
    assert "retargets B's PR to `dev`" in text
    assert "${" not in text
    entry = json.loads(read(work, LOCK_NAME))["files"]["CONTRIBUTING.md"]
    assert entry["mode"] == "block"
    assert sorted(entry["blocks"]) == sorted(BLOCKS)
    assert {b["layer"] for b in entry["blocks"].values()} == {"profiles/dev-main"}


def test_an_existing_contributing_keeps_its_own_sections(clone, github):
    work = clone("work")
    own = "# Contributing\n\nOur intro.\n\n## Developing locally\n\nRun `make dev`.\n"
    (work / "CONTRIBUTING.md").write_text(own, encoding="utf-8")

    changed, _said, _asked = install(work, github)

    text = read(work, "CONTRIBUTING.md")
    assert text.startswith(own)
    assert all(f"<!-- <<< repo-kit: {name} -->" in text for name in BLOCKS)
    assert "updated CONTRIBUTING.md" in changed

    # The repo's own text can change around the blocks; the next install leaves it be.
    edited = text.replace("Our intro.", "Our longer intro.") + "\n## Our own rules\n"
    (work / "CONTRIBUTING.md").write_text(edited, encoding="utf-8")
    changed, _said, _asked = install(work, github)
    assert changed == []
    assert read(work, "CONTRIBUTING.md") == edited


def test_a_second_install_changes_nothing(clone, github, only_main):
    work = clone("work")
    install(work, github)
    changed, _said, asked = install(work, github)
    assert changed == []
    assert asked == []


def test_a_repo_with_only_main_gets_dev_from_main(clone, github, only_main):
    work = clone("work")

    changed, said, _asked = install(work, github)

    assert remote_sha(work, "dev") == remote_sha(work, "main")
    assert "created `dev` on origin from `main`" in changed
    assert "isn't the default branch yet" in said


def test_prs_are_retargeted_only_after_a_yes(clone, github, only_main):
    work = clone("work")
    push_branch(work, "feat/a", start="origin/main")
    push_branch(work, "feat/b", start="origin/feat/a")
    github.add_pr("feat/a", base="main")
    github.add_pr("feat/b", base="feat/a")
    git(work, "switch", "-q", "main")

    _changed, said, asked = install(work, github, answer=False)

    assert len(asked) == 1
    assert "#1  feat/a" in said
    assert "#2" not in said
    assert github.get(1).base_ref == "main"

    # Declined once, it is offered again: install looks at the PRs as they are now.
    changed, _said, asked = install(work, github, answer=True)

    assert len(asked) == 1
    assert changed == ["retargeted #1 (feat/a) to `dev`"]
    assert github.get(1).base_ref == "dev"
    # A PR chained on another branch keeps its base.
    assert github.get(2).base_ref == "feat/a"
    assert install(work, github, answer=True)[2] == []


def test_the_release_pr_from_dev_is_never_offered(clone, github):
    work = clone("work")
    github.add_pr("dev", base="main")
    _changed, _said, asked = install(work, github, answer=True)
    assert asked == []
    assert github.get(1).base_ref == "main"


def test_an_existing_config_is_kept(clone, github):
    work = clone("work")
    text = 'version = "1.4.0"\n\n[worktree]\ncheck = "./check.sh"\n'
    (work / ".repo-kit.toml").write_text(text, encoding="utf-8")
    install(work, github)
    assert read(work, ".repo-kit.toml") == text
    assert ".repo-kit.toml" in json.loads(read(work, LOCK_NAME))["files"]


def test_an_unreleased_rk_refuses_to_seed_a_pin(clone, github, only_main, monkeypatch):
    monkeypatch.setattr(repo_kit, "__version__", "0.0.0")
    work = clone("work")

    with pytest.raises(RkError, match="unreleased build"):
        install(work, github)

    assert untouched(work)
    assert remote_sha(work, "dev") is None
    # With a pin already written, the same rk installs.
    (work / ".repo-kit.toml").write_text('version = "1.4.0"\n', encoding="utf-8")
    install(work, github)
    assert (work / CALLER).is_file()


def test_existing_settings_keep_their_keys_and_choices(clone, github):
    work = clone("work")
    (work / ".claude").mkdir()
    before = {
        "permissions": {"allow": ["Bash(ls)"]},
        "enabledPlugins": {"repo-kit@repo-kit": False, "other@market": True},
    }
    (work / ".claude/settings.json").write_text(json.dumps(before), encoding="utf-8")

    changed, _said, _asked = install(work, github)

    after = json.loads(read(work, ".claude/settings.json"))
    assert list(after) == ["permissions", "enabledPlugins", "extraKnownMarketplaces"]
    assert after["permissions"] == before["permissions"]
    # The repo had turned the plugin off: that is its choice.
    assert after["enabledPlugins"] == before["enabledPlugins"]
    assert "repo-kit" in after["extraKnownMarketplaces"]
    [backup_line] = [line for line in changed if line.startswith("backed up .claude/settings.json")]
    assert ".git" in backup_line


@pytest.mark.parametrize("content", ["{not json", "[]", '{"enabledPlugins": []}'])
def test_unusable_settings_stop_the_install_before_any_write(clone, github, only_main, content):
    work = clone("work")
    (work / ".claude").mkdir()
    (work / ".claude/settings.json").write_text(content, encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "chore: settings")

    with pytest.raises(RkError, match=r"\.claude/settings\.json"):
        install(work, github)

    assert untouched(work)
    assert remote_sha(work, "dev") is None


def test_a_refused_file_stops_the_install_before_any_write(clone, github, only_main):
    work = clone("work")
    (work / ".github/workflows").mkdir(parents=True)
    (work / CALLER).write_text("name: our own\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "ci: our own caller")

    with pytest.raises(SyncError, match="refused .github/workflows/update-prs.yml"):
        install(work, github)

    assert untouched(work)
    assert remote_sha(work, "dev") is None


def test_an_origin_with_neither_branch_is_refused(clone, github, origin):
    work = clone("work")
    git(origin, "update-ref", "-d", "refs/heads/dev")
    git(origin, "update-ref", "-d", "refs/heads/main")

    with pytest.raises(RkError, match="neither `dev` nor `main`"):
        install(work, github)

    assert untouched(work)


def run_bootstrap(work, github, setup, user_config, tmp_path):
    changed: list[str] = []
    said: list[str] = []
    bootstrap.bootstrap(
        work,
        "local",
        "demo",
        github,
        setup,
        user_config,
        source=tmp_path / "config.toml",
        profile="dev-main",
        changed=changed,
        confirm=lambda _question: False,
        say=said.append,
    )
    return changed, "\n".join(said)


def test_bootstrap_installs_then_enrols_the_repo(clone, github, tmp_path):
    work = clone("work")
    key = tmp_path / "bot.pem"
    key.write_text(KEY, encoding="utf-8")
    user_config = UserConfig(pr_bot=PrBotConfig(client_id="Iv23abc", private_key=str(key)))
    setup = FakeRepoSetup()

    changed, said = run_bootstrap(work, github, setup, user_config, tmp_path)

    assert (work / CALLER).is_file()
    assert setup.vars == {bootstrap.CLIENT_ID_VARIABLE: "Iv23abc"}
    assert setup.secrets == {bootstrap.PRIVATE_KEY_SECRET: KEY}
    assert changed.index("created .repo-kit.toml") < changed.index("created label needs-merge")
    assert "[OK]   managed files:" in said  # doctor ran last


def test_bootstrap_without_the_bot_credentials_writes_nothing(clone, github, only_main, tmp_path):
    work = clone("work")
    setup = FakeRepoSetup()

    with pytest.raises(RkError, match="needs gth-pr-bot's credentials"):
        run_bootstrap(work, github, setup, UserConfig(), tmp_path)

    assert untouched(work)
    assert remote_sha(work, "dev") is None
    assert setup.writes == []
