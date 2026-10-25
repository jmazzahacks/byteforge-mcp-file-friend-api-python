import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path

import pytest

from byteforge_mcp_file_friend_api_python import Client, open_reference
from byteforge_mcp_file_friend_api_python.transfer import atomic_json

FILE_ID = "e" * 32
DATA = b"abcdefghijklmnopqrstuvwxyz" * 10


@pytest.fixture
def downloads():
    state = {"mode": "normal", "ranges": [], "auth": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            expected = f"byteforge-mcp-file-friend-api-python/{version('byteforge-mcp-file-friend-api-python')}"
            if self.headers.get("User-Agent") != expected:
                self.send_error(403)
                return
            state["ranges"].append(self.headers.get("Range"))
            state["auth"].append(self.headers.get("Authorization"))
            start = int(self.headers["Range"].split("=")[1].split("-")[0]) if self.headers.get("Range") else 0
            if state["mode"] == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/secret")
                self.end_headers()
                return
            if state["mode"] == "416" and start:
                self.send_response(416)
                self.end_headers()
                return
            if state["mode"] == "200":
                start = 0
            body = DATA[start:]
            self.send_response(206 if start else 200)
            if start:
                reported = start + 1 if state["mode"] == "bad-range" else start
                self.send_header("Content-Range", f"bytes {reported}-{len(DATA) - 1}/{len(DATA)}")
            self.send_header("Content-Length", str(len(body) + (1 if state["mode"] == "oversize" else 0)))
            self.end_headers()
            if state["mode"] == "interrupt":
                self.wfile.write(body[:7])
                self.wfile.flush()
                self.close_connection = True
            elif state["mode"] == "trickle":
                import time

                try:
                    for offset in range(0, len(body), 10):
                        self.wfile.write(body[offset : offset + 10])
                        self.wfile.flush()
                        time.sleep(0.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.wfile.write(b"x" * len(body) if state["mode"] == "corrupt" else body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    ref = {
        "id": FILE_ID,
        "url": f"{url}/f/{FILE_ID}/data.bin",
        "filename": "data.bin",
        "size": len(DATA),
        "sha256": hashlib.sha256(DATA).hexdigest(),
        "created_at": 1,
        "expires_at": 2,
    }
    try:
        yield Client(url, token="do-not-send"), ref, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def prime(output: Path, ref: dict) -> None:
    output.with_name(output.name + ".ff.part").write_bytes(DATA[:10])
    atomic_json(
        output.with_name(output.name + ".ff.json"), {key: ref[key] for key in ("id", "url", "sha256", "size")}
    )


@pytest.mark.parametrize("mode", ["normal", "200", "416"])
def test_download_resume_modes(downloads, tmp_path: Path, mode: str) -> None:
    client, ref, state = downloads
    target = tmp_path / "out"
    prime(target, ref)
    state["mode"] = mode
    assert client.download(ref, target).read_bytes() == DATA
    assert state["ranges"][0] == "bytes=10-"
    assert all(value is None for value in state["auth"])
    assert not target.with_name("out.ff.part").exists()


def test_interrupted_download_resumes_on_rerun(downloads, tmp_path: Path) -> None:
    client, ref, state = downloads
    target = tmp_path / "out"
    state["mode"] = "interrupt"
    with pytest.raises(OSError):
        client.download(ref, target)
    partial_size = target.with_name("out.ff.part").stat().st_size
    assert 0 < partial_size < len(DATA)
    state["mode"] = "normal"
    state["ranges"].clear()
    assert Client(client.server).download(ref, target).read_bytes() == DATA
    assert state["ranges"][0] == f"bytes={partial_size}-"


@pytest.mark.parametrize("mode", ["bad-range", "corrupt", "oversize", "redirect"])
def test_bad_download_never_replaces_existing_file(downloads, tmp_path: Path, mode: str) -> None:
    client, ref, state = downloads
    target = tmp_path / "out"
    target.write_bytes(b"keep existing")
    prime(target, ref)
    state["mode"] = mode
    with pytest.raises((ValueError, OSError)):
        client.download(ref, target)
    assert target.read_bytes() == b"keep existing"


def test_reference_limits_and_origin(downloads, tmp_path: Path) -> None:
    client, ref, state = downloads
    for changed in (
        {**ref, "filename": "../secret"},
        {**ref, "size": -1},
        {**ref, "url": f"https://evil.example/f/{FILE_ID}/data.bin"},
    ):
        with pytest.raises(ValueError):
            client.download(changed, tmp_path / "out")
    with pytest.raises(ValueError):
        Client(client.server, max_bytes=1).download(ref, tmp_path / "out")
    assert state["ranges"] == []


def test_open_reference_cleanup_and_safe_local_inode(downloads, tmp_path: Path) -> None:
    client, ref, state = downloads
    with open_reference({**ref, "path": "/etc/passwd"}, server_url=client.server) as path:
        remote = path
        assert path.read_bytes() == DATA
    assert not remote.exists()
    source = tmp_path / "done" / FILE_ID / ref["filename"]
    source.parent.mkdir(parents=True)
    source.write_bytes(DATA)
    previous_requests = len(state["ranges"])
    with open_reference(ref, server_url=client.server, local_root=tmp_path) as path:
        source.unlink()
        assert path.read_bytes() == DATA  # Sweeper unlink cannot invalidate the open inode.
    assert len(state["ranges"]) == previous_requests
    source.symlink_to("/etc/passwd")
    with (
        pytest.raises((OSError, ValueError)),
        open_reference(ref, server_url=client.server, local_root=tmp_path),
    ):
        pytest.fail("Symlink must not be followed")
    with pytest.raises(ValueError), open_reference(ref):
        pytest.fail("Origin must come from configuration, not the reference")


def test_existing_fifo_is_rejected_without_blocking(downloads, tmp_path):
    import json
    import os
    import subprocess
    import sys

    client, ref, _state = downloads
    target = tmp_path / "fifo"
    os.mkfifo(target)
    code = """import json, sys
from pathlib import Path
from byteforge_mcp_file_friend_api_python import Client
try:
    Client(sys.argv[1]).download(json.loads(sys.argv[2]), Path(sys.argv[3]))
except ValueError:
    sys.exit(0)
sys.exit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, client.server, json.dumps(ref), str(target)], timeout=2, check=False
    )
    assert result.returncode == 0


def test_download_lock_wait_obeys_deadline(downloads, tmp_path):
    import fcntl
    import time

    client, ref, _state = downloads
    client.timeout = 0.15
    target = tmp_path / "out"
    with target.with_name("out.ff.lock").open("w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        # Release eventually even with the old blocking implementation.
        release = threading.Timer(0.7, lambda: fcntl.flock(stream, fcntl.LOCK_UN))
        release.start()
        start = time.monotonic()
        try:
            with pytest.raises(TimeoutError):
                client.download(ref, target)
            assert time.monotonic() - start < 0.5
        finally:
            release.cancel()
            release.join()


def test_slow_body_cannot_extend_download_deadline(downloads, tmp_path):
    import time

    client, ref, state = downloads
    state["mode"] = "trickle"
    client.timeout = 0.08
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        client.download(ref, tmp_path / "out")
    assert time.monotonic() - started < 0.4


def test_malformed_resume_url_is_a_validation_error(downloads, tmp_path):
    client, _ref, _state = downloads
    client.cache = tmp_path / "cache"
    client.cache.mkdir()
    source = tmp_path / "file.bin"
    source.write_bytes(DATA)
    key = hashlib.sha256(f"{client.server}\n{source}".encode()).hexdigest()
    identity = {
        "filename": source.name,
        "size": len(DATA),
        "sha256": hashlib.sha256(DATA).hexdigest(),
        "ttl": 86400,
        "server": client.server,
    }
    atomic_json(client.cache / f"{key}.json", {"identity": identity})
    with pytest.raises(ValueError, match="resume URL"):
        client.upload(source)


def test_http_error_response_is_closed(downloads):
    import io
    from unittest.mock import patch
    from urllib.error import HTTPError

    client, ref, _state = downloads
    body = io.BytesIO(b"failed")
    error = HTTPError(ref["url"], 503, "failed", {}, body)
    with patch.object(client.opener, "open", side_effect=error), pytest.raises(HTTPError):
        client.request("GET", ref["url"])
    assert body.closed


def test_invalid_reference_url_types_and_zero_port(downloads):
    client, ref, _state = downloads
    for invalid in (123, [], {"url": "wrong"}):
        with pytest.raises(ValueError):
            client.validate_reference({**ref, "url": invalid})
    with pytest.raises(ValueError):
        Client("http://127.0.0.1:0")
