import json
import threading
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

import src.api as api


@pytest.fixture
def run_import(monkeypatch):
    run = Mock()
    monkeypatch.setattr(api, "run_import", run)
    return run


@pytest.fixture
def server(run_import):
    httpd = api.create_server("127.0.0.1", 0, "secret-token")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def call(server, method, path, *, payload=None, token="secret-token"):
    host, port = server.server_address
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://{host}:{port}{path}", data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_health_does_not_require_a_token(server, run_import):
    status, body = call(server, "GET", "/health", token=None)

    assert (status, body) == (200, {"status": "ok"})
    run_import.assert_not_called()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, []),
        ({"mode": "upload"}, []),
        ({"mode": "process-only"}, ["--process-only"]),
        ({"mode": "send-report"}, ["--send-report"]),
        ({"mode": "send-empty-report"}, ["--send-empty-report"]),
    ],
)
def test_run_maps_mode_to_main_arguments(server, run_import, payload, expected):
    status, body = call(server, "POST", "/run", payload=payload)

    assert status == 200
    assert body == {"status": "ok", "mode": payload["mode"] if payload else "upload"}
    run_import.assert_called_once_with(expected)


@pytest.mark.parametrize("token", [None, "wrong-token", ""])
def test_run_requires_the_configured_token(server, run_import, token):
    status, body = call(server, "POST", "/run", payload={"mode": "upload"}, token=token)

    assert status == 401
    assert body == {"error": "Unauthorized"}
    run_import.assert_not_called()


def test_unknown_mode_is_rejected(server, run_import):
    status, body = call(server, "POST", "/run", payload={"mode": "delete-everything"})

    assert status == 400
    assert "Unknown mode" in body["error"]
    run_import.assert_not_called()


def test_non_string_mode_is_rejected(server, run_import):
    status, _ = call(server, "POST", "/run", payload={"mode": ["upload"]})

    assert status == 400
    run_import.assert_not_called()


def test_invalid_json_is_rejected(server, run_import):
    host, port = server.server_address
    request = urllib.request.Request(
        f"http://{host}:{port}/run",
        data=b"not-json",
        method="POST",
        headers={"Authorization": "Bearer secret-token"},
    )

    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)

    assert error.value.code == 400
    run_import.assert_not_called()


@pytest.mark.parametrize("error", [SystemExit(1), RuntimeError("boom")])
def test_failed_run_returns_server_error(server, run_import, error):
    run_import.side_effect = error

    status, body = call(server, "POST", "/run", payload={"mode": "upload"})

    assert status == 500
    assert body == {"status": "failed"}


def test_concurrent_run_is_rejected(server, run_import):
    started = threading.Event()
    release = threading.Event()

    def blocking_run(argv):
        started.set()
        release.wait(timeout=5)

    run_import.side_effect = blocking_run
    results = []

    def first_run():
        results.append(call(server, "POST", "/run", payload={"mode": "upload"}))

    thread = threading.Thread(target=first_run)
    thread.start()
    try:
        assert started.wait(timeout=5)
        status, body = call(server, "POST", "/run", payload={"mode": "upload"})
        assert status == 409
        assert body == {"error": "A run is already in progress"}
    finally:
        release.set()
        thread.join(timeout=5)

    assert results == [(200, {"status": "ok", "mode": "upload"})]


def test_unknown_path_returns_not_found(server):
    status, body = call(server, "GET", "/missing", token=None)

    assert (status, body) == (404, {"error": "Not found"})


def test_missing_token_prevents_startup(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setattr(api, "load_dotenv", Mock())

    with pytest.raises(ValueError, match="API_TOKEN"):
        api.load_api_token()
