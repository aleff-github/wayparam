from wayparam.filters import FilterOptions, is_boring, parse_ext_set


def test_parse_ext_set():
    s = parse_ext_set(".png,jpg,css")
    assert s == {".png", ".jpg", ".css"}


def test_is_boring_ext_blacklist():
    opt = FilterOptions(ext_blacklist={".png"})
    assert is_boring("https://example.com/a.png", opt)
    assert not is_boring("https://example.com/a", opt)
