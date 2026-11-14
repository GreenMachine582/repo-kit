import pytest

from repo_kit.bootstrap import parse_version, set_pin_text
from repo_kit.errors import RkError


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1.2.3", (1, 2, 3)), ("v1.2.3", (1, 2, 3)), (" v10.0.21 ", (10, 0, 21))],
)
def test_parse_version_accepts_releases(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "1.2", "v1.2.3.4", "latest", "1.2.3-rc1", "vv1.2.3"])
def test_parse_version_refuses_the_rest(text):
    with pytest.raises(RkError, match="vX.Y.Z"):
        parse_version(text)


def test_set_pin_keeps_comments_and_other_keys():
    text = '# pin\nversion = "1.0.0"  # moved by rk upgrade\nprofile = "dev-main"\n'
    assert set_pin_text(text, "1.1.0") == (
        '# pin\nversion = "1.1.0"  # moved by rk upgrade\nprofile = "dev-main"\n'
    )


def test_set_pin_keeps_single_quotes():
    assert set_pin_text("version = '1.0.0'\n", "1.1.0") == "version = '1.1.0'\n"


def test_set_pin_ignores_version_inside_a_table():
    text = 'version = "1.0.0"\n[project]\nversion = "9"\n'
    assert set_pin_text(text, "1.1.0") == 'version = "1.1.0"\n[project]\nversion = "9"\n'


def test_set_pin_handles_crlf():
    old = 'version = "1.0.0"\r\nx = 1\r\n'
    assert set_pin_text(old, "1.1.0") == 'version = "1.1.0"\r\nx = 1\r\n'


def test_set_pin_handles_no_final_newline():
    assert set_pin_text('version = "1.0.0"', "1.1.0") == 'version = "1.1.0"'


def test_set_pin_refuses_a_missing_line():
    with pytest.raises(RkError, match="exactly one"):
        set_pin_text('[project]\nversion = "1.0.0"\n', "1.1.0")


def test_set_pin_refuses_a_repeated_line():
    with pytest.raises(RkError, match="exactly one"):
        set_pin_text('version = "1.0.0"\nversion = "1.0.1"\n', "1.1.0")


def test_set_pin_refuses_an_odd_form():
    with pytest.raises(RkError, match="plain quoted"):
        set_pin_text("version = 1\n", "1.1.0")
