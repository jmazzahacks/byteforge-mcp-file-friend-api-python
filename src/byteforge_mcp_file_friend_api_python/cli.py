"""CLI for resumable transfer, status and shared-administration deletion."""

import argparse
import http.client
import json
import os
import sys
import tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from byteforge_mcp_file_friend_models import validate_id


def file_id_from_reference(value: str, server: str) -> str:
    if "://" not in value:
        return validate_id(value)
    parsed, origin = urlsplit(value), urlsplit(server)
    if (
        (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc)
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Reference must belong to the configured File Friend server")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "f" or not parts[2]:
        raise ValueError("Expected a File Friend download URL")
    return validate_id(parts[1])


def call(server: str, file_id: str, method: str, token: str) -> tuple[dict, int]:
    parsed = urlsplit(server)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("FF_SERVER_URL must be an HTTP(S) origin")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("HTTP is only permitted for loopback development")
    headers = {"Accept": "application/json"}
    if method == "DELETE":
        if not token:
            raise ValueError("FF_TOKEN is required for deletion")
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        f"{server.rstrip('/')}/api/files/{validate_id(file_id)}", method=method, headers=headers
    )
    from byteforge_mcp_file_friend_api_python.transfer import Client

    client = Client(server, token, timeout=30)
    opener = client.opener
    try:
        response = opener.open(request, timeout=30)
    except HTTPError as error:
        if error.code != 410:
            error.close()
            raise
        response = error
    with response:
        body = client.response_bytes(response, 65537)
        if len(body) > 65536:
            raise ValueError("Status response exceeds size limit")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise TypeError("Status response must be an object")
        return result, response.status


def quantity(value: str) -> int:
    import re

    match = re.fullmatch(r"(\d+)([KMGTkmgt]?)", value)
    if not match:
        raise argparse.ArgumentTypeError("Use bytes or an integer with K/M/G/T suffix")
    return int(match[1]) * 1024 ** ("KMGT".find(match[2].upper()) + 1 if match[2] else 0)


def duration(value: str) -> int:
    import re

    match = re.fullmatch(r"(\d+)([smhd]?)", value)
    if not match:
        raise argparse.ArgumentTypeError("Use seconds or an integer with s/m/h/d suffix")
    return int(match[1]) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]


def progress(done: int, total: int) -> None:
    print(f"{done}/{total} bytes", file=sys.stderr)


def main() -> None:
    from byteforge_mcp_file_friend_api_python.transfer import Client

    parser = argparse.ArgumentParser(description="Resumable, verified File Friend transfers")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("info", "rm"):
        command = commands.add_parser(name)
        command.add_argument("reference")
    upload = commands.add_parser("upload")
    upload.add_argument("file", type=Path)
    upload.add_argument("--ttl", type=duration, default=86400)
    upload.add_argument("--chunk-size", type=quantity, default=64 * 1024**2)
    upload.add_argument("--restart", action="store_true")
    upload.add_argument("--timeout", type=float, default=3600)
    download = commands.add_parser("download")
    download.add_argument("reference")
    download.add_argument("-o", "--output", type=Path)
    download.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()
    try:
        path = Path.home() / ".config" / "ff" / "config.toml"
        settings = tomllib.loads(path.read_text()) if path.exists() else {}
        server = os.environ.get("FF_SERVER_URL", settings.get("server_url", ""))
        token = os.environ.get("FF_TOKEN", settings.get("token", ""))
        if not server:
            raise ValueError("FF_SERVER_URL or config.toml server_url is required")
        cache = os.environ.get("FF_CACHE_DIR", settings.get("cache_dir"))
        client = Client(
            server,
            token,
            cache_dir=Path(cache) if cache else None,
            timeout=getattr(args, "timeout", 30),
            progress=progress,
            max_bytes=int(os.environ.get("FF_MAX_BYTES", settings.get("max_bytes", 50 * 1024**3))),
        )
        if args.command == "upload":
            print("Hashing and uploading file", file=sys.stderr)
            result = client.upload(
                args.file, ttl=args.ttl, chunk_size=args.chunk_size, restart=args.restart
            ).to_dict()
        elif args.command == "download":
            result = {"path": str(client.download(args.reference, args.output))}
        else:
            result, status = call(
                server, client.file_id(args.reference), "GET" if args.command == "info" else "DELETE", token
            )
            if status == 410:
                print(json.dumps(result))
                raise SystemExit(1)
        print(json.dumps(result))
    except KeyboardInterrupt:
        print("ff: interrupted; rerun the command to resume", file=sys.stderr)
        raise SystemExit(130) from None
    except (ValueError, TypeError, OSError, URLError, http.client.HTTPException) as error:
        message = str(error) if isinstance(error, (ValueError, TypeError)) else type(error).__name__
        print(f"ff: {message}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
