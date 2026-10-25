"""Exercise the SDK identity at the HTTP boundary, including authenticated methods."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version

import pytest

from byteforge_mcp_file_friend_api_python import Client


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PATCH", "DELETE", "OPTIONS"])
def test_sdk_identity_on_wire(method: str) -> None:
    received = []
    expected = f"byteforge-mcp-file-friend-api-python/{version('byteforge-mcp-file-friend-api-python')}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def handle_request(self) -> None:
            received.append(
                (self.command, self.headers.get_all("User-Agent"), self.headers.get("Authorization"))
            )
            self.send_response(204 if self.headers.get("User-Agent") == expected else 403)
            self.end_headers()

        do_GET = do_HEAD = do_POST = do_PATCH = do_DELETE = do_OPTIONS = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = Client(f"http://127.0.0.1:{server.server_port}", token="synthetic-token")
        authenticated = method != "GET"
        with client.request(method, client.server + "/probe", authenticated=authenticated) as response:
            assert response.status == 204
        assert received == [(method, [expected], "Bearer synthetic-token" if authenticated else None)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
