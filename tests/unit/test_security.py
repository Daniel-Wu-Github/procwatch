import pytest

from procwatch.security import request_allowed

PORT = 8765
GOOD = {"Host": f"127.0.0.1:{PORT}"}
KW = dict(expected_token="s3cret", port=PORT)


def test_a_get_with_the_right_token_and_host_is_allowed():
    assert request_allowed("GET", GOOD, "s3cret", **KW) is True


def test_localhost_host_is_also_allowed():
    assert request_allowed("GET", {"Host": f"localhost:{PORT}"}, "s3cret", **KW) is True


@pytest.mark.parametrize("token", [None, "", "wrong", "s3cret ", "S3CRET"])
def test_missing_or_wrong_token_is_rejected(token):
    assert request_allowed("GET", GOOD, token, **KW) is False


@pytest.mark.parametrize("host", ["evil.example.com", f"evil.example.com:{PORT}", "127.0.0.1", f"127.0.0.1:{PORT + 1}", "", None])
def test_a_foreign_or_missing_host_is_rejected_even_with_the_token(host):
    headers = {} if host is None else {"Host": host}
    assert request_allowed("GET", headers, "s3cret", **KW) is False


def test_header_names_are_case_insensitive():
    assert request_allowed("GET", {"host": f"127.0.0.1:{PORT}"}, "s3cret", **KW) is True


def test_a_post_needs_a_same_origin_origin_header():
    ok = {**GOOD, "Origin": f"http://127.0.0.1:{PORT}"}
    assert request_allowed("POST", ok, "s3cret", **KW) is True
    assert request_allowed("POST", {**GOOD, "Origin": f"http://localhost:{PORT}"}, "s3cret", **KW) is True


@pytest.mark.parametrize("origin", [None, "null", "http://evil.example.com", f"http://evil.example.com:{PORT}", f"https://127.0.0.1:{PORT}", f"http://127.0.0.1:{PORT + 1}"])
def test_a_post_with_a_missing_or_foreign_origin_is_rejected(origin):
    headers = dict(GOOD)
    if origin is not None:
        headers["Origin"] = origin
    assert request_allowed("POST", headers, "s3cret", **KW) is False


def test_a_get_ignores_the_origin_header():
    assert request_allowed("GET", {**GOOD, "Origin": "http://evil.example.com"}, "s3cret", **KW) is True
