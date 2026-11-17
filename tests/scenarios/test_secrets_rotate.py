"""`rk secrets rotate` finding the repos it names: real clones, a fake home, no real store."""

from __future__ import annotations

from harness import git

from repo_kit.config import UserConfig
from repo_kit.credentials import PR_BOT, RELEASE_BOT, configure_commands, known_repos


def test_known_repos_finds_the_cwd_repo_and_those_with_worktrees(clone, tmp_path):
    here = clone("here")
    other = clone("other")
    (other / ".repo-kit.toml").write_text('version = "0.1.0"\n[update_prs]\nenabled = false\n')
    wt_root = tmp_path / "wt"
    (wt_root / "o-other").mkdir(parents=True)
    git(other, "worktree", "add", "-q", "-b", "feat/x", str(wt_root / "o-other" / "feat-x"))
    config = UserConfig(worktree_root=wt_root)

    repos = known_repos(config, here)

    assert {r.checkout for r in repos} == {here, other}
    by_checkout = {r.checkout: r for r in repos}
    assert PR_BOT in by_checkout[here].apps
    assert by_checkout[other].apps == (RELEASE_BOT,)  # update-prs is off there

    pr_commands = configure_commands(PR_BOT, repos)
    assert pr_commands == [f'cd "{here}" && rk configure --replace-key']


def test_known_repos_skips_a_folder_that_is_not_a_repo(tmp_path):
    wt_root = tmp_path / "wt"
    (wt_root / "junk" / "x").mkdir(parents=True)
    assert known_repos(UserConfig(worktree_root=wt_root), tmp_path / "nowhere") == []
