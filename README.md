# File Friend Python client

Resumable uploads/downloads through `Client` and `open_reference`. The only runtime dependency is the models package pinned to its reviewed GitHub commit; no core, PostgreSQL or Flask installation is required. MIT licensed.

## Integration guide

See [the integration guide](docs/INTEGRATION.md) for the production reference contract,
producer/consumer examples, the released reviewed-reply email integration and its pilot test plan.

## Command-line application

The `ff` executable is now maintained separately in
[byteforge-mcp-file-friend-cli](https://github.com/jmazzahacks/byteforge-mcp-file-friend-cli).
Install that application on hosts that upload or download files from a shell.
This SDK supplies `Client` and `open_reference`; version 0.0.3 no longer installs
an `ff` command or the `byteforge_mcp_file_friend_api_python.cli` module.
Existing `FF_*` settings and resume caches remain compatible.

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

The virtual environment lives at this repository root. On a fresh clone, create
it with `uv venv --allow-existing --python 3.13 .`, preserving and restoring the
tracked `.gitignore` if recreating it.

```bash
source bin/activate
uv pip install --require-hashes -r dev-requirements.txt -r build-requirements.txt
uv pip install --no-build-isolation 'git+https://github.com/jmazzahacks/byteforge-mcp-file-friend-models.git@1282a681a41cd144b21593ab0820a724b9a485a6'
uv build --wheel --no-build-isolation
uv pip install --no-deps dist/byteforge_mcp_file_friend_api_python-0.0.3-py3-none-any.whl
python -m pytest
ruff check src tests
```

Tests use the installed wheel, not source-path overrides, and exclude the parent workspace's database fixture. Third-party tooling pins and hashes are retained from validated workspace locks; refresh with the seven-day release-age gate. Cross-repository dependencies use token-free immutable GitHub revisions, never sibling editable/path dependencies.

## Validation

SDK 0.0.3 passed all 23 isolated tests and lint against its installed wheel.
Transfer, resume, integrity, origin, redirect and request-identity coverage remains
in the SDK; CLI-specific checks live in the CLI repository. These local checks do
not establish production throughput or a live email pilot.

Licensed under MIT; see `LICENSE`. Created by Jason Byteforge (@jmazzahacks).
