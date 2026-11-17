"""The pure parts of `rk fleet`: rows, the doctor summary, PR text and repo selection."""

import json

import pytest

from repo_kit import fleet
from repo_kit.config import ChecksConfig, FleetConfig, RepoConfig, UserConfig
from repo_kit.errors import RkError
from repo_kit.fleet import RepoResult, Status


def doc(*checks):
    return json.dumps({"ok": True, "checks": [{"name": n, "status": s} for n, s in checks]})


def test_summary_is_the_worst_status_and_names_what_is_wrong():
    assert fleet.summarise_doctor(doc(("a", "OK"), ("b", "SKIP"))) == (Status.OK, "2 check(s)")
    assert fleet.summarise_doctor(doc(("a", "OK"), ("b", "WARN"))) == (
        Status.WARN,
        "2 check(s); WARN: b",
    )
    status, detail = fleet.summarise_doctor(doc(("a", "FAIL"), ("b", "WARN"), ("c", "WARN")))
    assert status is Status.FAIL
    assert detail == "3 check(s); FAIL: a; WARN: b, c"


def test_summary_caps_the_names_shown():
    names = [(f"c{i}", "WARN") for i in range(7)]
    assert fleet.summarise_doctor(doc(*names))[1].endswith("WARN: c0, c1, c2, c3 (+3)")


@pytest.mark.parametrize("text", ["", "[]", '{"ok": true}', '{"checks": [{"status": "OK"}]}'])
def test_summary_refuses_a_document_it_cant_read(text):
    with pytest.raises(RkError, match="wasn't the document expected"):
        fleet.summarise_doctor(text)


def test_the_table_aligns_rows_indents_extra_lines_and_counts():
    text = fleet.render(
        [
            RepoResult("o/a", Status.OK, "in sync"),
            RepoResult("o/long-name", Status.FAIL, "first\nsecond"),
        ]
    )
    assert text.splitlines() == [
        "o/a          OK     in sync",
        "o/long-name  FAIL   first",
        "                    second",
        "2 repo(s): 1 ok, 1 fail",
    ]


def test_only_fail_makes_the_run_fail():
    rows = [
        RepoResult("a", Status.WARN),
        RepoResult("b", Status.PLAN),
        RepoResult("c", Status.SKIP),
    ]
    assert not fleet.failed(rows)
    assert fleet.failed([*rows, RepoResult("d", Status.FAIL)])


def test_the_pr_title_follows_the_repos_rule():
    plain = RepoConfig()
    conventional = RepoConfig(checks=ChecksConfig(pr_title="conventional"))
    assert fleet.pr_title(plain, "1.2.3") == "Move repo-kit to 1.2.3"
    assert fleet.pr_title(conventional, "1.2.3") == "chore: move repo-kit to 1.2.3"


def test_the_pr_body_lists_the_files_and_says_who_opened_it():
    body = fleet.pr_body("1.2.3", "1.2.0", ["a.yml", ".repo-kit.lock"])
    assert "from 1.2.0 to 1.2.3" in body
    assert "- `a.yml`\n- `.repo-kit.lock`" in body
    assert "Opened by `rk fleet upgrade`" in body
    assert "(only the pin)" in fleet.pr_body("1.2.3", "1.2.0", [])


def test_select_keeps_config_order_and_matches_case_insensitively(tmp_path):
    config = UserConfig(fleet=FleetConfig({"O/a": tmp_path, "O/b": tmp_path, "O/c": tmp_path}))
    assert list(fleet.select(config, [])) == ["O/a", "O/b", "O/c"]
    assert list(fleet.select(config, ["o/c", "O/A"])) == ["O/a", "O/c"]
    with pytest.raises(RkError, match="not in"):
        fleet.select(config, ["o/z"])
