# File Friend Python client — development guide

Independent client repository using ByteForge python-lib-setup. Python package `byteforge_mcp_file_friend_api_python`, CLI `ff`. Runtime dependency is the immutable GitHub models revision only. Never import core, backend, Flask or PostgreSQL. Keep redirects disabled and preserve trusted-origin validation, bounded transfers, SHA-256 verification and resume behavior.

Root virtual environment at `bin/activate`; preserve `.gitignore` when recreating it. Tests use the installed wheel and local HTTP fixtures, with no database required. Follow README for development commands. Do not change CLI/wire contracts during extraction. Never modify another agent's code or infrastructure. MIT/public under jmazzahacks approved; this checkpoint is local.
