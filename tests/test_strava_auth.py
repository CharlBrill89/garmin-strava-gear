"""The OAuth callback server, driven with a simulated browser."""

import importlib.util
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sa():
    spec = importlib.util.spec_from_file_location("strava_auth", ROOT / "strava_auth.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Class-level state persists across tests; reset it.
    module._CallbackHandler.code = None
    module._CallbackHandler.error = None
    module._CallbackHandler.state = None
    module._CallbackHandler.scope = ""
    return module


def _serve(sa):
    """Start the callback server on a free port; returns (server, thread, port)."""
    handler = sa._CallbackHandler
    server = HTTPServer(("localhost", 0), handler)
    server.timeout = 0.2

    def loop(deadline):
        while time.monotonic() < deadline and not (handler.code or handler.error):
            server.handle_request()

    thread = threading.Thread(target=loop, args=(time.monotonic() + 10,), daemon=True)
    thread.start()
    time.sleep(0.1)
    return server, thread, server.server_address[1]


def test_callback_ignores_favicon_and_captures_code(sa):
    handler = sa._CallbackHandler
    server, thread, port = _serve(sa)

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://localhost:{port}/favicon.ico", timeout=3)
    assert exc.value.code == 404
    assert handler.code is None and handler.error is None  # favicon must not poison state

    page = urllib.request.urlopen(
        f"http://localhost:{port}/callback?code=TESTCODE&state=abc&scope=read,activity:read_all",
        timeout=3,
    ).read().decode()
    thread.join(timeout=5)
    server.server_close()

    assert "Authorised" in page
    assert handler.code == "TESTCODE"
    assert handler.state == "abc"
    assert "activity:read_all" in handler.scope


def test_callback_error_is_escaped(sa):
    handler = sa._CallbackHandler
    server, thread, port = _serve(sa)

    page = urllib.request.urlopen(
        f"http://localhost:{port}/callback?error=%3Cscript%3Ex", timeout=3
    ).read().decode()
    thread.join(timeout=5)
    server.server_close()

    assert handler.error == "<script>x"
    assert "<script>" not in page  # rendered escaped, not as markup
