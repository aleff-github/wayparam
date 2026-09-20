from wayparam.wayback import (
    CdxOptions,
    _split_captures_and_resume_key,
    _split_urls_and_resume_key,
    parse_capture_line,
)


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


def test_parse_capture_metadata_line():
    record = parse_capture_line(
        "20240102030405 200 text/html https://example.com/path?q=hello world"
    )
    assert record is not None
    assert record.timestamp == "20240102030405"
    assert record.status_code == "200"
    assert record.mime_type == "text/html"
    assert record.original == "https://example.com/path?q=hello world"


def test_parse_capture_metadata_maps_dash_to_none():
    record = parse_capture_line("20240102030405 - - https://example.com/?q=1")
    assert record is not None
    assert record.status_code is None
    assert record.mime_type is None


def test_split_capture_metadata_resume_key():
    text = (
        "20200101000000 200 text/html https://example.com/?a=1\n"
        "20210101000000 302 text/html https://example.com/?a=2\n"
        "example%2Ccom%29%2F+20210101000000%21\n"
    )
    captures, resume_key = _split_captures_and_resume_key(text, CdxOptions(metadata=True))
    assert [capture.timestamp for capture in captures] == ["20200101000000", "20210101000000"]
    assert resume_key == "example%2Ccom%29%2F+20210101000000%21"
