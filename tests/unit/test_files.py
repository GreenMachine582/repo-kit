"""The shared-files engine's pieces: placeholders, manifests, markers, the lock, templates."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_kit.config import ExtrasConfig, RepoConfig
from repo_kit.files import (
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
        ('[files."a"]\nmode = "block"\n', "needs `block`"),
        ('[files."a"]\nmode = "managed"\nblock = "x"\n', "needs `block`"),
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
        listed = {t.source for t in load_layer(root, layer)}
        present = {p for p in manifest.parent.rglob("*") if p.is_file() and p.name != MANIFEST_NAME}
        assert listed == present, layer


def test_every_real_template_renders():
    config = RepoConfig(version="1.2.3", extras=ExtrasConfig(use_local_gth=True))
    variables = template_variables(config, "o", "r")
    for template in collect_templates(templates_root(), config):
        rendered = render(template.source.read_text(encoding="utf-8"), variables, template.path)
        assert "${version}" not in rendered


def test_the_update_prs_caller_pins_one_version():
    variables = template_variables(RepoConfig(version="1.2.3"), "o", "r")
    [caller] = [
        t
        for t in collect_templates(templates_root(), RepoConfig())
        if t.path == ".github/workflows/update-prs.yml"
    ]
    text = render(caller.source.read_text(encoding="utf-8"), variables, caller.path)
    assert "update-prs.yml@v1.2.3" in text
    assert "ref: v1.2.3" in text
    assert "${{ vars.GTH_PR_BOT_CLIENT_ID }}" in text
