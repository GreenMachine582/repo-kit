"""The shared-files engine's pieces: placeholders, manifests, markers, the lock, templates."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from repo_kit.checks import REQUIRED_CHECKS
from repo_kit.config import ChecksConfig, ExtrasConfig, ReleaseConfig, RepoConfig
from repo_kit.files import (
    LOCK_NAME,
    MANIFEST_NAME,
    SyncError,
    block_inner,
    collect_templates,
    digest,
    find_block,
    layers_for,
    load_layer,
    lock_text,
    markers,
    read_lock,
    render,
    template_variables,
    templates_root,
    with_block,
)

VARS = {"version": "1.2.3", "repo": "demo"}


def test_only_lowercase_braced_names_are_placeholders():
    text = "v${version} ${{ github.sha }} $HOME ${HOME} $$ ${repo}"
    assert render(text, VARS, "t") == "v1.2.3 ${{ github.sha }} $HOME ${HOME} $$ demo"


def test_an_unknown_placeholder_is_an_error():
    with pytest.raises(SyncError, match=r"unknown placeholder \$\{verison\}"):
        render("${verison}", VARS, "t")


def test_variables_default_the_project_name_to_the_repo():
    variables = template_variables(RepoConfig(version="1.0.0"), "o", "r")
    assert variables["project_name"] == "r"
    assert variables["default_base"] == "dev"
    assert variables["release_branch"] == "main"
    assert variables["back_merge_into"] == "dev"


def test_variables_carry_the_commit_and_pr_title_rules():
    variables = template_variables(RepoConfig(version="1.0.0"), "o", "r")
    assert variables["commit_header_max"] == "100"
    assert variables["pr_title_rule"].startswith("PR titles are **not** Conventional Commits")

    config = RepoConfig(checks=ChecksConfig(pr_title="conventional", commit_header_max=72))
    variables = template_variables(config, "o", "r")
    assert variables["commit_header_max"] == "72"
    assert variables["pr_title_rule"].startswith("PR titles are Conventional Commits too")


def test_the_dev_main_profile_holds_the_contributing_blocks_in_reading_order():
    (contributing,) = [
        t for t in collect_templates(templates_root(), RepoConfig()) if t.path == "CONTRIBUTING.md"
    ]
    assert contributing.layer == "profiles/dev-main"
    assert [b.name for b in contributing.blocks] == [
        "branches",
        "pull-requests",
        "chained-prs",
        "releasing",
    ]


def write_layer(root: Path, layer: str, manifest: str, files: dict[str, str]) -> None:
    folder = root / layer
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MANIFEST_NAME).write_text(manifest, encoding="utf-8")
    for name, content in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(content, encoding="utf-8")


def test_a_layer_without_a_manifest_has_no_templates(tmp_path):
    assert load_layer(tmp_path, "common") == []


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ('[files."a"]\nmode = "copied"\n', "mode must be one of"),
        ('[files."a"]\nmode = "block"\n', "needs `blocks`"),
        ('[files."a"]\nmode = "managed"\nblocks = { x = "a" }\n', "needs `blocks`"),
        ('[files."a"]\nmode = "block"\nblocks = "a"\n', "must be a table"),
        ('[files."a"]\nmode = "block"\nblocks = {}\n', "must be a table"),
        ('[files."a"]\nmode = "block"\nblocks = { Bad_Name = "a" }\n', "lowercase words"),
        ('[files."a"]\nmode = "block"\nblocks = { x = "missing" }\n', "no template file"),
        ('[files."a"]\nmode = "block"\nblocks = { x = "a" }\nsource = "a"\n', "not `source`"),
        ('[files."a"]\nmode = "managed"\nblock = "x"\n', "unknown key"),
        ("files = 1\n", "must be a table"),
        ('[files."a"]\nmode = "managed"\ncolour = 1\n', "unknown key"),
        ('[files."../a"]\nmode = "managed"\n', "relative path inside the repo"),
        ('[files."missing"]\nmode = "managed"\n', "no template file"),
        ("version = 1\n", "unknown key"),
    ],
)
def test_bad_manifests_are_errors(tmp_path, manifest, message):
    write_layer(tmp_path, "common", manifest, {"a": "x\n"})
    with pytest.raises(SyncError, match=message):
        load_layer(tmp_path, "common")


def test_a_path_claimed_by_two_layers_is_an_error(tmp_path):
    entry = '[files."a"]\nmode = "managed"\n'
    write_layer(tmp_path, "common", entry, {"a": "x\n"})
    write_layer(tmp_path, "profiles/dev-main", entry, {"a": "y\n"})
    with pytest.raises(SyncError, match="claimed by both"):
        collect_templates(tmp_path, RepoConfig())


BLOCK_A = '[files."a"]\nmode = "block"\nblocks = { one = "one.txt" }\n'


def test_two_layers_can_each_add_blocks_to_one_file(tmp_path):
    write_layer(tmp_path, "common", BLOCK_A, {"one.txt": "1\n"})
    profile = '[files."a"]\nmode = "block"\nblocks = { two = "two.txt" }\n'
    write_layer(tmp_path, "profiles/dev-main", profile, {"two.txt": "2\n"})
    [template] = collect_templates(tmp_path, RepoConfig())
    assert [(b.name, b.layer) for b in template.blocks] == [
        ("one", "common"),
        ("two", "profiles/dev-main"),
    ]


def test_a_block_claimed_by_two_layers_is_an_error(tmp_path):
    write_layer(tmp_path, "common", BLOCK_A, {"one.txt": "1\n"})
    write_layer(tmp_path, "profiles/dev-main", BLOCK_A, {"one.txt": "2\n"})
    with pytest.raises(SyncError, match="block `one` is claimed by both"):
        collect_templates(tmp_path, RepoConfig())


def test_a_block_file_and_a_managed_file_cannot_share_a_path(tmp_path):
    write_layer(tmp_path, "common", BLOCK_A, {"one.txt": "1\n"})
    write_layer(tmp_path, "profiles/dev-main", '[files."a"]\nmode = "managed"\n', {"a": "x\n"})
    with pytest.raises(SyncError, match="claimed by both"):
        collect_templates(tmp_path, RepoConfig())


@pytest.mark.parametrize("lock", ["[]", '{"files": []}', '{"files": {"a": "managed"}}'])
def test_a_malformed_lock_is_an_error(tmp_path, lock):
    (tmp_path / LOCK_NAME).write_text(lock, encoding="utf-8")
    with pytest.raises(SyncError, match=r"\.repo-kit\.lock"):
        read_lock(tmp_path)


def test_extras_add_their_layer():
    config = RepoConfig(extras=ExtrasConfig(use_local_gth=True))
    assert layers_for(config)[-1] == "extras/use-local-gth"


def test_markers_use_html_comments_in_markdown():
    assert markers("CONTRIBUTING.md", "branches")[0] == "<!-- >>> repo-kit: branches -->"
    assert markers(".gitignore", "local") == ("# >>> repo-kit: local", "# <<< repo-kit: local")


def test_a_block_is_appended_then_replaced_in_place():
    text = "mine\n"
    added = with_block(text, ".gitignore", "local", "a\n", None)
    assert added == "mine\n\n# >>> repo-kit: local\na\n# <<< repo-kit: local\n"
    span = find_block(added, ".gitignore", "local")
    assert block_inner(added, span) == "a\n"
    replaced = with_block(added + "after\n", ".gitignore", "local", "b\nc\n", span)
    assert replaced == "mine\n\n# >>> repo-kit: local\nb\nc\n# <<< repo-kit: local\nafter\n"


def test_a_block_in_an_empty_file_has_no_leading_blank_line():
    assert with_block("", ".gitignore", "x", "a\n", None).startswith("# >>> repo-kit: x")


@pytest.mark.parametrize(
    "text",
    [
        "# >>> repo-kit: local\n",
        "# <<< repo-kit: local\n# >>> repo-kit: local\n",
        "# >>> repo-kit: local\n# >>> repo-kit: local\n# <<< repo-kit: local\n",
    ],
)
def test_broken_markers_are_errors(text):
    with pytest.raises(SyncError, match="markers"):
        find_block(text, ".gitignore", "local")


def test_hashing_ignores_crlf():
    assert digest("a\r\nb\r\n") == digest("a\nb\n")


def test_the_lock_is_sorted_json_ending_in_a_newline():
    text = lock_text({"version": "1", "files": {"b": {"mode": "managed"}, "a": {}}})
    assert text.endswith("}\n")
    assert text.index('"a"') < text.index('"b"')


def test_every_real_manifest_matches_its_files():
    root = templates_root()
    for manifest in root.rglob(MANIFEST_NAME):
        layer = manifest.parent.relative_to(root).as_posix()
        listed = {source for t in load_layer(root, layer) for source in t.sources}
        present = {p for p in manifest.parent.rglob("*") if p.is_file() and p.name != MANIFEST_NAME}
        assert listed == present, layer


def test_every_real_template_renders():
    config = RepoConfig(version="1.2.3", extras=ExtrasConfig(use_local_gth=True))
    variables = template_variables(config, "o", "r")
    for template in collect_templates(templates_root(), config):
        for source in template.sources:
            rendered = render(source.read_text(encoding="utf-8"), variables, template.path)
            assert "${version}" not in rendered


def test_the_update_prs_caller_pins_one_version():
    variables = template_variables(RepoConfig(version="1.2.3"), "o", "r")
    [caller] = [
        t
        for t in collect_templates(templates_root(), RepoConfig())
        if t.path == ".github/workflows/update-prs.yml"
    ]
    [source] = caller.sources
    text = render(source.read_text(encoding="utf-8"), variables, caller.path)
    assert "update-prs.yml@v1.2.3" in text
    assert "ref: v1.2.3" in text
    assert "${{ vars.GTH_PR_BOT_CLIENT_ID }}" in text


def render_caller(path: str, config: RepoConfig | None = None) -> str:
    config = config or RepoConfig(version="1.2.3")
    variables = template_variables(config, "o", "r")
    [caller] = [t for t in collect_templates(templates_root(), config) if t.path == path]
    [source] = caller.sources
    return render(source.read_text(encoding="utf-8"), variables, caller.path)


def test_the_release_caller_pins_one_version_and_back_merges_into_dev():
    text = render_caller(".github/workflows/release.yml")
    assert "repo-kit/.github/workflows/release.yml@v1.2.3" in text
    assert "branches: [main]" in text
    assert "target-branch: main" in text
    assert 'back-merge-into: "dev"' in text
    assert "${{ vars.GTH_RELEASE_BOT_CLIENT_ID }}" in text
    assert "${{ secrets.GTH_RELEASE_BOT_PRIVATE_KEY }}" in text


def test_the_release_caller_can_turn_the_back_merge_off():
    config = RepoConfig(version="1.2.3", release=ReleaseConfig(back_merge=False))
    assert 'back-merge-into: ""' in render_caller(".github/workflows/release.yml", config)


def test_the_pr_checks_caller_pins_one_version():
    text = render_caller(".github/workflows/pr-checks.yml")
    assert "repo-kit/.github/workflows/pr-checks.yml@v1.2.3" in text
    assert "ref: v1.2.3" in text
    assert "${{ github.event.pull_request.number }}" in text


def job_ids(workflow: str) -> list[str]:
    """The keys directly under `jobs:` (two-space indent). The stdlib has no YAML parser."""
    jobs = workflow.split("\njobs:\n", 1)[1]
    return re.findall(r"^  ([a-z][a-z0-9_-]*):$", jobs, flags=re.MULTILINE)


def test_the_required_check_names_match_the_workflows():
    caller = render_caller(".github/workflows/pr-checks.yml")
    reusable = (Path(__file__).parents[2] / ".github/workflows/pr-checks.yml").read_text(
        encoding="utf-8"
    )
    names = tuple(f"{outer} / {inner}" for outer in job_ids(caller) for inner in job_ids(reusable))
    assert names == REQUIRED_CHECKS
