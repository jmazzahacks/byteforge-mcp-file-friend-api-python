# File Friend Python client

Resumable uploads/downloads, the `ff` CLI and `open_reference`. The only runtime dependency is the models package pinned to its reviewed GitHub commit; no core, PostgreSQL or Flask installation is required. MIT licensed.

## CLI


Requires Python 3.13+ on a Unix system with `fcntl`; follow the local installation steps below until the client remote is published. Configure `FF_SERVER_URL` and `FF_TOKEN`, or `~/.config/ff/config.toml`:

```toml
server_url = "https://ff.example.com"
# token = "..."  # Protect this file if it contains a token.
```

Environment variables override the file. Loopback HTTP is allowed for local testing. Upload and deletion require the token; status and downloads do not send it.

```bash
ff upload ./report.pdf --ttl 24h
ff download <id-or-url> -o ./report.pdf
ff info <id-or-url>
ff rm <id-or-url>
```

Commands emit JSON on stdout and progress/errors on stderr. Upload returns a ready reference only after verification. Interrupted uploads resume using a locked cache keyed by server and source path, with full SHA-256/size/options checked before reuse. Changed sources require `--restart`. Lost PATCH responses recover via HEAD. `--chunk-size` defaults to 64 MiB; `--timeout` defaults to one hour and should be increased for slow or very large transfers.

Downloads resume through Range requests, verify the complete SHA-256, and atomically replace the destination only after verification. Redirects, foreign origins and malformed references are rejected. Partial files remain for retries. Lock acquisition and response-body reads respect the transfer deadline; special files are rejected. Small lock files intentionally remain beside destinations and in the upload cache to preserve coordination. Configure cache and size limits with `FF_CACHE_DIR` and `FF_MAX_BYTES`.

## Tool helper

```python
from pathlib import Path
from byteforge_mcp_file_friend_api_python import open_reference

with open_reference(reference, server_url="https://ff.example.com") as path:
    process_file(path)

# Tools sharing the trusted server volume can avoid copying:
with open_reference(reference, server_url="https://ff.example.com",
                    local_root=Path("/data/ff")) as path:
    process_file(path)
```

The trusted server may also come from `FF_SERVER_URL`. The helper ignores reference-supplied local paths. It validates identity, size and SHA-256; local traversal rejects symlinks. On Linux, a held descriptor keeps verified bytes accessible even if expiry unlinks the file. Otherwise it downloads into a private temporary directory. Use the returned path only inside the context; temporary bytes are removed on exit. `max_bytes` and `timeout` bound processing.

## Local development and validation

The virtual environment lives at this repository root. Preserve `.gitignore` when recreating it.

```bash
source bin/activate
uv pip install --require-hashes -r dev-requirements.txt -r build-requirements.txt
uv pip install --no-build-isolation 'git+https://github.com/jmazzahacks/byteforge-mcp-file-friend-models.git@1282a681a41cd144b21593ab0820a724b9a485a6'
uv build --wheel --no-build-isolation
uv pip install --no-deps dist/byteforge_mcp_file_friend_api_python-0.0.1-py3-none-any.whl
python -m pytest
ruff check src tests
ff --help
```

Tests use the installed wheel, not source-path overrides, and exclude the parent workspace's database fixture. Third-party tooling pins and hashes are retained from validated workspace locks; refresh with the seven-day release-age gate. Cross-repository dependencies use token-free immutable GitHub revisions, never sibling editable/path dependencies.

## Extraction status

Imports now use `byteforge_mcp_file_friend_api_python`; CLI commands, `FF_*` settings, reference JSON and resume file names remain unchanged. The client owns its redirect-blocking handler, preserving the original behavior without importing core. The legacy workspace implementation remains intact until final consumer migration.

Public repositories under `jmazzahacks` and MIT licensing are approved. This checkpoint remains local; no client remote has been created.

## Extraction validation

The installed client wheel passed 20 tests plus lint with no core, backend, Flask or PostgreSQL driver installed. The `ff` entry point was verified. Installed as a test artifact in the backend environment, the wheel also enabled all 33 backend tests and nine crash subtests to pass with real tusd and disposable PostgreSQL, with no skips. This included subprocess CLI upload/download, interrupted upload recovery, `open_reference` and an actual PostgreSQL restart during an upload. No sibling path dependency was added to package metadata; reproducible backend test dependency pinning follows client publication.
