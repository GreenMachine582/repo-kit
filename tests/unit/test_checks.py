"""The commit and PR title rules in `checks.py`."""

from __future__ import annotations

import pytest

from repo_kit.checks import TYPES, check_header, check_pr_title, header_of, parse_header


@pytest.mark.parametrize("kind", TYPES)
def test_every_type_parses(kind):
    header = parse_header(f"{kind}: do the thing")
    assert header is not None
    assert (header.type, header.scope, header.breaking) == (kind, None, False)


def test_scope_and_breaking_mark():
    header = parse_header("feat(api)!: drop v1")
    assert header is not None
    assert (header.type, header.scope, header.breaking, header.subject) == (
        "feat",
        "api",
        True,
        "drop v1",
    )


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("Add a thing", "not a conventional commit"),
        ("feature: add a thing", "unknown type `feature`"),
        ("Feat: add a thing", "the type must be lower case: `feat`"),
        ("feat:add a thing", "put a space after the `:`"),
        ("feat:  add a thing", "use exactly one space after the `:`"),
        ("feat: ", "the description after `: ` is empty"),
        ("feat(api) add a thing", "not a conventional commit"),
        ("", "the first line is empty"),
        ('Revert "feat: add a thing"', "not a conventional commit"),
    ],
)
def test_bad_headers_say_why(line, problem):
    assert parse_header(line) is None
    (found,) = check_header(line, 100)
    assert found.startswith(problem)


def test_header_length_limit():
    at_limit = "feat: " + "x" * 94
    assert len(at_limit) == 100
    assert check_header(at_limit, 100) == []
    assert check_header(at_limit + "x", 100) == ["the first line is 101 characters, over 100"]


def test_a_long_bad_header_reports_both_problems():
    assert len(check_header("x" * 120, 100)) == 2


@pytest.mark.parametrize(
    ("message", "header"),
    [
        ("feat: x\n\nbody\n", "feat: x"),
        ("\n\n  \nfeat: x\n", "feat: x"),
        ("# Please enter the commit message\nfeat: x\n# comment\n", "feat: x"),
        ("feat: x\r\n\r\nbody\r\n", "feat: x"),
        ("feat: x  \n", "feat: x"),
        ("# only comments\n", ""),
        (
            "# ------------------------ >8 ------------------------\ndiff --git a b\n",
            "",
        ),
    ],
)
def test_header_of_reads_the_first_line_git_keeps(message, header):
    assert header_of(message) == header


@pytest.mark.parametrize(
    "title", ["Add the folder picker", "Fix the flaky test", "Feature flags: a first cut"]
)
def test_plain_titles_pass_not_conventional(title):
    assert check_pr_title(title, "not-conventional") is None


@pytest.mark.parametrize("title", ["feat: add x", "Feat: add x", "fix(api)!: y", "CHORE: z"])
def test_conventional_titles_fail_not_conventional(title):
    problem = check_pr_title(title, "not-conventional")
    assert problem is not None
    assert "count that change twice" in problem


@pytest.mark.parametrize("rule", ["not-conventional", "conventional"])
@pytest.mark.parametrize("title", ["release: 1.4.0", "chore(main): release 1.4.0"])
def test_release_titles_pass_either_rule(rule, title):
    assert check_pr_title(title, rule) is None


def test_the_conventional_rule_requires_a_conventional_title():
    assert check_pr_title("feat: add x", "conventional") is None
    problem = check_pr_title("Add x", "conventional")
    assert problem is not None
    assert problem.startswith("the PR title must be a conventional commit")
