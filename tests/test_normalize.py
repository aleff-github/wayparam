from wayparam.normalize import NormalizeOptions, canonicalize_url


def test_canonicalize_masks_values_and_sorts_and_drops_default_port():
    opt = NormalizeOptions(
        placeholder="FUZZ", keep_values=False, only_params=True, drop_tracking=False
    )
    u = "https://EXAMPLE.com:443/path?b=2&a=1"
    out = canonicalize_url(u, opt)
    assert out == "https://example.com/path?a=FUZZ&b=FUZZ"


def test_canonicalize_drops_tracking_params():
    opt = NormalizeOptions(drop_tracking=True)
    u = "https://example.com/?utm_source=x&gclid=y&id=1"
    out = canonicalize_url(u, opt)
    assert out == "https://example.com/?id=FUZZ"


def test_only_params_filters_urls_without_query():
    opt = NormalizeOptions(only_params=True)
    assert canonicalize_url("https://example.com/path", opt) is None


def test_identical_parameter_pairs_are_collapsed():
    """Masking turns ?id=1&id=2&id=3 into one pair, not three identical ones."""
    opt = NormalizeOptions()
    assert canonicalize_url("https://example.com/p?id=1&id=2&id=3&x=9", opt) == (
        "https://example.com/p?id=FUZZ&x=FUZZ"
    )


def test_distinct_values_survive_when_values_are_kept():
    opt = NormalizeOptions(keep_values=True)
    assert canonicalize_url("https://example.com/p?t=a&t=b&t=a", opt) == (
        "https://example.com/p?t=a&t=b"
    )


def test_canonicalize_supports_ipv6_and_drops_default_port():
    opt = NormalizeOptions(drop_tracking=False)
    assert canonicalize_url("https://[2001:DB8::1]:443/p?id=1", opt) == (
        "https://[2001:db8::1]/p?id=FUZZ"
    )


def test_canonicalize_keeps_non_default_ipv6_port():
    opt = NormalizeOptions(drop_tracking=False)
    assert canonicalize_url("https://[2001:DB8::1]:8443/p?id=1", opt) == (
        "https://[2001:db8::1]:8443/p?id=FUZZ"
    )


def test_canonicalize_rejects_an_invalid_port():
    assert canonicalize_url("https://example.com:not-a-port/?id=1", NormalizeOptions()) is None
