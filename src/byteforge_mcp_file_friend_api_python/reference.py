"""Resolve an untrusted reference to verified bytes for a bounded context lifetime."""

import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from byteforge_mcp_file_friend_models import FileReference

from byteforge_mcp_file_friend_api_python.transfer import Client


@contextmanager
def open_reference(
    reference: dict | FileReference,
    *,
    server_url: str | None = None,
    local_root: Path | None = None,
    max_bytes: int = 50 * 1024**3,
    timeout: float = 3600,
) -> Iterator[Path]:
    """Never trust reference['path']. local_root is the configured FF_DATA_DIR.

    Local Linux references yield /proc/<pid>/fd/<fd>, keeping the verified inode
    alive even when the sweeper unlinks its name. The returned path is valid only
    inside this context. Otherwise bytes are downloaded to a private temp directory.
    """
    configured = server_url or os.environ.get("FF_SERVER_URL")
    if not configured:
        raise ValueError("Configure a trusted File Friend server URL")
    client = Client(configured, timeout=timeout, max_bytes=max_bytes)
    ref = client.validate_reference(reference)
    descriptors = []
    local = None
    try:
        if local_root is not None:
            try:
                directory = os.open(local_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                descriptors.append(directory)
                for part in ("done", ref.id):
                    directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    descriptors.append(directory)
                fd = os.open(ref.filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                descriptors.append(fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size != ref.size:
                    raise ValueError("Local reference has an invalid type or size")
                with os.fdopen(os.dup(fd), "rb") as stream:
                    if client.digest(stream) != ref.sha256:
                        raise ValueError("Local reference failed SHA-256 verification")
                os.lseek(fd, 0, os.SEEK_SET)
                local = Path(f"/proc/{os.getpid()}/fd/{fd}")
                if not local.exists():
                    local = None  # Non-Linux fallback uses the normal verified download path.
            except FileNotFoundError:
                local = None
        if local is not None:
            yield local
        else:
            with tempfile.TemporaryDirectory(prefix="ff-reference-") as temporary:
                yield client.download(ref, Path(temporary) / ref.filename)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
