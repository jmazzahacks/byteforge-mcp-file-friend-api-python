"""Bounded tus uploads and verified HTTP Range downloads with durable resume state."""

import base64
import fcntl
import hashlib
import http.client
import json
import math
import os
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, build_opener

from byteforge_mcp_file_friend_models import FileReference, validate_id, validate_metadata

from byteforge_mcp_file_friend_api_python._http import NoRedirects

BLOCK = 1024 * 1024
MAX_CHUNK = 64 * BLOCK
NETWORK_ERRORS = (OSError, URLError, http.client.HTTPException)


def origin(url: str) -> tuple:
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError("Invalid service URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port == 0
    ):
        raise ValueError("Invalid service URL")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("HTTP is permitted only for loopback development")
    return (
        parsed.scheme,
        parsed.hostname.lower(),
        parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80),
    )


def signature(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def locked(path: Path, deadline: float) -> Iterator[None]:
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Transfer lock must be a regular file")
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Transfer deadline exceeded waiting for lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


def read_json(path: Path) -> dict:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Resume record must be a regular file")
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Resume record exceeds size limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise TypeError("Invalid resume record")
    return value


def atomic_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".ff-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Client:
    def __init__(
        self,
        server_url: str,
        token: str = "",
        *,
        cache_dir: Path | None = None,
        timeout: float = 3600,
        max_bytes: int = 50 * 1024**3,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.origin = origin(server_url)
        if urlsplit(server_url).path not in ("", "/"):
            raise ValueError("Server URL must be an origin without a path")
        if not math.isfinite(timeout) or timeout <= 0 or max_bytes < 0:
            raise ValueError("Invalid transfer limits")
        self.server = server_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.cache = cache_dir or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ff"
        self.progress = progress
        self.opener = build_opener(NoRedirects())
        self.deadline = time.monotonic() + timeout

    def check_time(self) -> None:
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Transfer deadline exceeded")

    def url(self, url: str, category: str | None = None) -> str:
        if origin(url) != self.origin:
            raise ValueError("Reference belongs to a different File Friend origin")
        if category:
            parts = unquote(urlsplit(url).path, errors="strict").strip("/").split("/")
            expected = 3 if category == "f" else 2
            if len(parts) != expected or parts[0] != category:
                raise ValueError("Invalid File Friend resource path")
            validate_id(parts[1])
        return url

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict | None = None,
        authenticated: bool = False,
    ) -> object:
        self.check_time()
        self.url(url)
        outgoing = {"Accept-Encoding": "identity", **(headers or {})}
        if authenticated:
            if not self.token:
                raise ValueError("FF_TOKEN is required")
            outgoing["Authorization"] = f"Bearer {self.token}"
        try:
            return self.opener.open(
                Request(url, data=body, method=method, headers=outgoing),
                timeout=max(0.01, min(30, self.deadline - time.monotonic())),
            )
        except HTTPError as error:
            error.close()
            raise

    def response_bytes(self, response: object, limit: int) -> bytes:
        chunks = []
        remaining = limit
        while remaining:
            self.check_time()
            block = response.read1(min(BLOCK, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        self.check_time()
        return b"".join(chunks)

    def json_request(self, method: str, url: str, authenticated: bool = False) -> dict:
        with self.request(method, url, authenticated=authenticated) as response:
            data = self.response_bytes(response, 65537)
        if len(data) > 65536:
            raise ValueError("Status response exceeds size limit")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise TypeError("Invalid status response")
        return value

    def file_id(self, value: str) -> str:
        if "://" not in value:
            return validate_id(value)
        self.url(value, "f")
        return unquote(urlsplit(value).path).strip("/").split("/")[1]

    def info(self, value: str) -> dict:
        self.deadline = time.monotonic() + self.timeout
        return self._info(value)

    def _info(self, value: str) -> dict:
        return self.json_request("GET", f"{self.server}/api/files/{self.file_id(value)}")

    def remove(self, value: str) -> dict:
        self.deadline = time.monotonic() + self.timeout
        return self.json_request("DELETE", f"{self.server}/api/files/{self.file_id(value)}", True)

    def validate_reference(self, value: dict | FileReference) -> FileReference:
        try:
            reference = FileReference.from_dict(
                value.to_dict() if isinstance(value, FileReference) else value
            )
        except (KeyError, TypeError) as error:
            raise ValueError("Malformed file reference") from error
        self.url(reference.url, "f")
        parts = unquote(urlsplit(reference.url).path, errors="strict").strip("/").split("/")
        if parts[1:] != [reference.id, reference.filename]:
            raise ValueError("Reference URL does not match file identity")
        if reference.size > self.max_bytes:
            raise ValueError("File exceeds configured byte limit")
        return reference

    def reference(self, value: str | dict | FileReference) -> FileReference:
        self.deadline = time.monotonic() + self.timeout
        return self._reference(value)

    def _reference(self, value: str | dict | FileReference) -> FileReference:
        if not isinstance(value, str):
            return self.validate_reference(value)
        file_id = self.file_id(value)
        while True:
            self.check_time()
            status = self._info(file_id)
            if status.get("id") != file_id:
                raise ValueError("Status returned a different file identity")
            if status.get("state") == "ready":
                ref = self.validate_reference(status.get("reference"))
                if ref.id != file_id:
                    raise ValueError("Ready reference changed identity")
                return ref
            if status.get("state") not in ("uploading", "verifying", "publishing"):
                raise ValueError("File failed verification or is no longer available")
            time.sleep(min(0.2, max(0, self.deadline - time.monotonic())))

    def digest(self, stream: object) -> str:
        digest = hashlib.sha256()
        stream.seek(0)
        count = 0
        while block := stream.read(BLOCK):
            count += len(block)
            if count > self.max_bytes:
                raise ValueError("File exceeds configured byte limit")
            self.check_time()
            digest.update(block)
        return digest.hexdigest()

    def upload(
        self, path: Path, *, ttl: int = 86400, chunk_size: int = MAX_CHUNK, restart: bool = False
    ) -> FileReference:
        self.deadline = time.monotonic() + self.timeout
        if not self.token:
            raise ValueError("FF_TOKEN is required for upload")
        if not 1 <= chunk_size <= MAX_CHUNK:
            raise ValueError("Chunk size must be between 1 byte and 64 MiB")
        path = Path(path).absolute()
        self.cache.mkdir(parents=True, mode=0o700, exist_ok=True)
        key = hashlib.sha256(f"{self.server}\n{path}".encode()).hexdigest()
        record_path = self.cache / f"{key}.json"
        with (
            locked(self.cache / f"{key}.lock", self.deadline),
            os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream,
        ):
            initial = os.fstat(stream.fileno())
            if not stat.S_ISREG(initial.st_mode) or initial.st_size > self.max_bytes:
                raise ValueError("Source must be a regular file within the configured size limit")
            digest = self.digest(stream)
            values = validate_metadata({"filename": path.name, "sha256": digest, "ttl": ttl}, initial.st_size)
            identity = {**values, "server": self.server}
            if signature(initial) != signature(os.fstat(stream.fileno())):
                raise ValueError("Source changed while hashing")
            record = None
            if record_path.exists() and not restart:
                record = read_json(record_path)
                if record.get("identity") != identity:
                    raise ValueError("Source or upload options changed; use --restart for a new upload")
                if not isinstance(record.get("url"), str):
                    raise ValueError("Invalid resume URL; use --restart for a new upload")
                self.url(record["url"], "files")
            if record is None:
                record = self.create_upload(identity)
                atomic_json(record_path, record)
            url = record["url"]
            failures = 0
            recreated = False
            while True:
                try:
                    with self.request(
                        "HEAD", url, headers={"Tus-Resumable": "1.0.0"}, authenticated=True
                    ) as response:
                        offset = int(response.headers["Upload-Offset"])
                        length = int(response.headers["Upload-Length"])
                    if length != initial.st_size or not 0 <= offset <= length:
                        raise ValueError("Invalid upload offset or length")
                    if offset == length:
                        break
                    if signature(initial) != signature(os.fstat(stream.fileno())) or signature(
                        initial
                    ) != signature(path.stat()):
                        raise ValueError("Source changed during upload")
                    stream.seek(offset)
                    data = stream.read(min(chunk_size, length - offset))
                    if not data:
                        raise ValueError("Unexpected end of source")
                    with self.request(
                        "PATCH",
                        url,
                        body=data,
                        authenticated=True,
                        headers={
                            "Tus-Resumable": "1.0.0",
                            "Upload-Offset": str(offset),
                            "Content-Type": "application/offset+octet-stream",
                        },
                    ) as response:
                        if response.status != 204 or int(response.headers["Upload-Offset"]) != offset + len(
                            data
                        ):
                            raise ValueError("Invalid PATCH acknowledgement")
                    failures = 0
                    if self.progress:
                        self.progress(offset + len(data), length)
                except NETWORK_ERRORS as error:
                    if isinstance(error, HTTPError) and error.code in (404, 410) and not recreated:
                        record = self.create_upload(identity)
                        atomic_json(record_path, record)
                        url = record["url"]
                        recreated = True
                        continue
                    if isinstance(error, HTTPError) and error.code not in (408, 409, 429, 500, 502, 503, 504):
                        raise
                    failures += 1
                    if failures > 3:
                        raise
                    self.check_time()
                    time.sleep(0.1 * failures)
            if signature(initial) != signature(os.fstat(stream.fileno())) or signature(initial) != signature(
                path.stat()
            ):
                raise ValueError("Source changed during upload")
            file_id = urlsplit(url).path.rstrip("/").rsplit("/", 1)[1]
            reference = self._reference(file_id)
            if reference.sha256 != digest or reference.size != length:
                raise ValueError("Server reference does not match uploaded bytes")
            record_path.unlink(missing_ok=True)
            return reference

    def create_upload(self, identity: dict) -> dict:
        encoded = ",".join(
            f"{key} {base64.b64encode(str(identity[key]).encode()).decode()}"
            for key in ("filename", "sha256", "ttl")
        )
        with self.request(
            "POST",
            self.server + "/files/",
            body=b"",
            authenticated=True,
            headers={
                "Tus-Resumable": "1.0.0",
                "Upload-Length": str(identity["size"]),
                "Upload-Metadata": encoded,
            },
        ) as response:
            if response.status != 201 or not response.headers.get("Location"):
                raise ValueError("Invalid upload creation response")
            url = self.url(urljoin(self.server + "/files/", response.headers["Location"]), "files")
        return {"identity": identity, "url": url}

    def download(self, value: str | dict | FileReference, output: Path | None = None) -> Path:
        self.deadline = time.monotonic() + self.timeout
        ref = self._reference(value)
        output = Path(output) if output is not None else Path(ref.filename)
        if output.is_symlink():
            raise ValueError("Destination must not be a symlink")
        partial = output.with_name(output.name + ".ff.part")
        meta = output.with_name(output.name + ".ff.json")
        lock = output.with_name(output.name + ".ff.lock")
        identity = {key: getattr(ref, key) for key in ("id", "url", "sha256", "size")}
        with locked(lock, self.deadline):
            if output.exists():
                with os.fdopen(
                    os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
                ) as existing:
                    if not stat.S_ISREG(os.fstat(existing.fileno()).st_mode):
                        raise ValueError("Destination must be a regular file")
                    if (
                        os.fstat(existing.fileno()).st_size == ref.size
                        and self.digest(existing) == ref.sha256
                    ):
                        return output
            old = read_json(meta) if meta.exists() else None
            fd = os.open(partial, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(fd, "r+b") as stream:
                if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
                    raise ValueError("Partial file must be a private regular file")
                if old != identity or os.fstat(fd).st_size > ref.size:
                    stream.truncate(0)
                atomic_json(meta, identity)
                if os.fstat(fd).st_size == ref.size and self.digest(stream) != ref.sha256:
                    stream.truncate(0)
                self.receive(ref, stream)
                if os.fstat(fd).st_size != ref.size or self.digest(stream) != ref.sha256:
                    stream.truncate(0)
                    raise ValueError("Downloaded file failed SHA-256 verification")
                stream.flush()
                os.fsync(fd)
            os.replace(partial, output)
            directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            meta.unlink(missing_ok=True)
        return output

    def receive(self, ref: FileReference, stream: object) -> None:
        failures = 0
        range_reset = False
        while True:
            stream.seek(0, os.SEEK_END)
            offset = stream.tell()
            if offset == ref.size:
                return
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                with self.request("GET", ref.url, headers=headers) as response:
                    if response.headers.get("Content-Encoding", "identity") != "identity":
                        raise ValueError("Encoded downloads are not supported")
                    if response.status == 200:
                        stream.seek(0)
                        stream.truncate(0)
                        offset = 0
                        end = ref.size - 1
                    elif response.status == 206:
                        import re

                        match = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                        )
                        if not match:
                            raise ValueError("Invalid Content-Range")
                        start, end, total = map(int, match.groups())
                        if start != offset or total != ref.size or not start <= end < total:
                            raise ValueError("Range does not match requested file")
                    else:
                        raise ValueError("Unexpected download status")
                    expected = end - offset + 1
                    length = response.headers.get("Content-Length")
                    if length is not None and int(length) != expected:
                        raise ValueError("Content-Length does not match file range")
                    received = 0
                    while True:
                        self.check_time()
                        try:
                            block = response.read1(min(BLOCK, expected - received + 1))
                        except http.client.IncompleteRead as error:
                            block = error.partial
                            if received + len(block) > expected:
                                raise ValueError("Download exceeded declared size") from error
                            stream.write(block)
                            stream.flush()
                            raise
                        if not block:
                            break
                        if received + len(block) > expected:
                            raise ValueError("Download exceeded declared size")
                        stream.write(block)
                        stream.flush()
                        received += len(block)
                        if self.progress:
                            self.progress(offset + received, ref.size)
                    if received != expected:
                        raise ConnectionError("Interrupted download")
                    failures = 0
            except NETWORK_ERRORS as error:
                if isinstance(error, HTTPError):
                    if error.code == 416 and not range_reset:
                        stream.seek(0)
                        stream.truncate(0)
                        range_reset = True
                        continue
                    if error.code not in (408, 429, 500, 502, 503, 504):
                        raise
                failures += 1
                if failures > 3:
                    raise
                self.check_time()
                time.sleep(0.1 * failures)
