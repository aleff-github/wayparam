from wayparam.wayback import _split_urls_and_resume_key


def test_split_resume_key_explicit():
    text = "http://a\nhttp://b\nresumeKey: XYZ"
    urls, rk = _split_urls_and_resume_key(text)
    assert urls == ["http://a", "http://b"]
    assert rk == "XYZ"


def test_split_resume_key_heuristic():
    text = "http://a\nhttp://b\nXYZ"
    urls, rk = _split_urls_and_resume_key(text)
    assert urls == ["http://a", "http://b"]
    assert rk == "XYZ"
