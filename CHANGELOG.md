# Changelog

## 0.0.3

- Move the `ff` executable and CLI module to `byteforge-mcp-file-friend-cli`.
  Install the separate CLI application for shell use; Python API imports, wire
  contracts, configuration and transfer caches are unchanged.

## 0.0.2

- Identify all SDK and CLI requests with
  `User-Agent: byteforge-mcp-file-friend-api-python/<installed version>`.
  The shared HTTP opener covers uploads, status, deletion, full and resumed
  downloads, and `open_reference`. This fixes edge rejection of urllib's generic
  default identity without changing authentication or redirect handling.
- Added real HTTP regression checks for the supported methods and required SDK
  identity in the download/resume/helper fixtures and CLI status/deletion tests.

Consumers pinned to an earlier Git revision must update their dependency pin and
rebuild/redeploy their own application. No server change or new credential is needed.
This release does not claim an end-to-end email attachment pilot.
