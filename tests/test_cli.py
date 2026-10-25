import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path

import pytest

from byteforge_mcp_file_friend_api_python.cli import call, file_id_from_reference

FILE_ID = "c" * 32


def test_reference_origin_and_id_validation() -> None:
    assert file_id_from_reference(f"https://ff.example/f/{FILE_ID}/file", "https://ff.example") == FILE_ID
    for ref in ("../etc/passwd", f"https://evil.example/f/{FILE_ID}/file", f"file:///f/{FILE_ID}/file"):
        with pytest.raises(ValueError):
            file_id_from_reference(ref, "https://ff.example")


def test_token_not_sent_to_public_status_or_redirect(tmp_path: Path, monkeypatch) -> None:
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            requests.append((self.path, self.headers.get("Authorization"), self.headers.get("User-Agent")))
            if self.path.endswith(FILE_ID):
                body = json.dumps({"id": FILE_ID, "state": "uploading"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(302)
                self.send_header("Location", f"/api/files/{FILE_ID}")
                self.end_headers()

        def do_DELETE(self) -> None:
            requests.append((self.path, self.headers.get("Authorization"), self.headers.get("User-Agent")))
            self.send_response(302)
            self.send_header("Location", f"/api/files/{FILE_ID}")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert call(base, FILE_ID, "GET", "secret")[0]["state"] == "uploading"
        assert requests[-1][1] is None
        from urllib.error import HTTPError

        with pytest.raises(HTTPError):
            call(base, FILE_ID, "DELETE", "secret")
        assert len(requests) == 2  # Redirect never followed.
        monkeypatch.setenv("FF_SERVER_URL", base)
        monkeypatch.delenv("PYTHONPATH", raising=False)
        result = subprocess.run(
            [sys.executable, "-m", "byteforge_mcp_file_friend_api_python.cli", "info", FILE_ID],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout)["state"] == "uploading"
        assert not result.stderr
        expected = f"byteforge-mcp-file-friend-api-python/{version('byteforge-mcp-file-friend-api-python')}"
        assert all(request[2] == expected for request in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_client_status_can_be_reused_after_idle():
    import time
    from unittest.mock import patch

    from byteforge_mcp_file_friend_api_python import Client

    client = Client("https://ff.example")
    client.deadline = time.monotonic() - 1

    def status(method, url, authenticated=False):
        client.check_time()
        return {"id": FILE_ID, "state": "uploading"}

    with patch.object(client, "json_request", side_effect=status):
        assert client.info(FILE_ID)["state"] == "uploading"
        client.deadline = time.monotonic() - 1
        client.remove(FILE_ID)


def test_cli_handles_protocol_errors_without_traceback(tmp_path, monkeypatch, capsys):
    import http.client

    from byteforge_mcp_file_friend_api_python import Client
    from byteforge_mcp_file_friend_api_python.cli import main

    monkeypatch.setenv("FF_SERVER_URL", "https://ff.example")
    monkeypatch.setenv("FF_TOKEN", "test-token")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["ff", "upload", str(tmp_path / "file")])

    def broken_upload(*args, **kwargs):
        raise http.client.BadStatusLine("sensitive response text")

    monkeypatch.setattr(Client, "upload", broken_upload)
    with pytest.raises(SystemExit) as outcome:
        main()
    assert outcome.value.code == 1
    output = capsys.readouterr()
    assert "BadStatusLine" in output.err
    assert "sensitive response text" not in output.err
    assert "Traceback" not in output.err
