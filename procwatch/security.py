import hmac


def request_allowed(method, headers, token, *, expected_token, port):
    headers = {key.lower(): value for key, value in headers.items()}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if method not in {"GET", "POST"} or headers.get("host") not in hosts:
        return False
    if not isinstance(token, str) or not hmac.compare_digest(token.encode(), expected_token.encode()):
        return False
    return method == "GET" or headers.get("origin") in {f"http://{host}" for host in hosts}
