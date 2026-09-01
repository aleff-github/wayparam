from wayparam.config import build_filter_options
from wayparam.filters import FilterOptions, is_boring, parse_ext_set


def test_parse_ext_set():
    s = parse_ext_set(".png,jpg,css")
    assert s == {".png", ".jpg", ".css"}


def test_is_boring_ext_blacklist():
    opt = FilterOptions(ext_blacklist={".png"})
    assert is_boring("https://example.com/a.png", opt)
    assert not is_boring("https://example.com/a", opt)


def test_whitelist_overrides_the_default_blacklist():
    """`--ext-whitelist .png` must keep .png even though it is blacklisted by default."""
    opt = build_filter_options(ext_whitelist=".png,.php")
    assert not is_boring("https://example.com/a.png?x=1", opt)
    assert not is_boring("https://example.com/a.php?x=1", opt)
    assert is_boring("https://example.com/a.aspx?x=1", opt)
    # Extensionless paths are never judged by extension.
    assert not is_boring("https://example.com/a?x=1", opt)


def test_path_regex_still_applies_under_a_whitelist():
    opt = build_filter_options(ext_whitelist=".php", exclude_path_regex=["^/static/"])
    assert is_boring("https://example.com/static/a.php?x=1", opt)
    assert not is_boring("https://example.com/app/a.php?x=1", opt)
