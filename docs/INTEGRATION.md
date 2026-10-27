# File Friend integration guide

Audience: the `byteforge-email-mcp` team and other remote tools that consume files.

Contract reviewed: 2026-10-27. Production origin: **https://ff.mazza.vc**.

File Friend transfers file bytes outside the MCP conversation. A producer uploads a
file and hands a small JSON reference to another agent or tool. The receiving tool
resolves that reference into verified local bytes and performs its normal operation.
There is no separate File Friend MCP server required for this workflow.

## Available components and evidence

- Install [the `ff` CLI](https://github.com/jmazzahacks/byteforge-mcp-file-friend-cli#install-on-a-host)
  on hosts that upload/download from a shell.
- Install this Python SDK in applications that use `Client` or `open_reference`.
  SDK 0.0.3 no longer provides the CLI; its Python API is unchanged.
- Email MCP supports File Friend attachments in **reviewed replies** through
  `prepare_reply_draft(..., attachment_refs=...)`. New compose, reply-all, and
  legacy `send_message`/`create_draft` attachment extensions are outside that contract.

Evidence reviewed on 2026-10-27: email owner ticket
`b40bfaac-6ce6-4237-8738-29beed34925e` records deployed image 23 with SDK 0.0.2;
Apollo ticket `af47d722-b189-4f26-8899-5c2f728052ba` records a 69-byte synthetic
upload and verified download from `https://ff.mazza.vc`, followed by Rivet activation.
Those are recorded checks, not a new production test performed for this release.
No live email/draft/send pilot was exercised in those checks. 20–40 GB transfers
and sustained disk/load acceptance remain unverified. Backups remain deferred by
the project owner and are not a prerequisite for using this integration.

## The handoff

1. The producing agent uploads its local file using the CLI or Python client.
2. File Friend receives the upload, verifies SHA-256 and publishes the download.
3. The upload operation returns a ready `FileReference` object.
4. The agent passes that entire object as a tool argument. No file bytes or base64
   content belong in the MCP arguments.
5. The receiving tool calls `open_reference` with its configured File Friend origin.
6. Inside the context, it processes the verified file, or constructs and submits the
   email attachment. Temporary downloaded files are removed on context exit.

The receiving machine does not need the producer's filesystem, an Apollo volume
mount, a PostgreSQL connection or a File Friend administration token.

## Installation and configuration

The current Python client requires **Python 3.13+ and Unix `fcntl`**. It depends on
the shared models library, not the File Friend backend, PostgreSQL driver or Flask.
Activate the application's virtual environment first. Install the SDK release:


```bash
uv pip install 'byteforge-mcp-file-friend-api-python @ git+https://github.com/jmazzahacks/byteforge-mcp-file-friend-api-python.git@v0.0.3'
```

Release `v0.0.3` retains the SDK request identity fix introduced in 0.0.2.
For application dependency locks, resolve the release tag to its full Git commit
and pin that immutable SHA. SDK metadata already pins the compatible models
revision. Never use sibling paths or an unreviewed branch head. Install the CLI
separately for shell commands; receiving Python services do not need it.

| Setting | Producer / uploader | Receiving tool |
|---|---|---|
| `FF_SERVER_URL` | `https://ff.mazza.vc` | Same trusted origin, configured by the operator |
| `FF_TOKEN` | Gatekeeper API key with the required service grants | Not needed for reference downloads |
| `FF_CACHE_DIR` | Optional CLI upload-resume cache location | Not needed for `open_reference` temporary downloads |
| `FF_MAX_BYTES` | Optional CLI byte limit | Pass `max_bytes` explicitly to the helper; this CLI env setting does not configure it |

Always start with HTTPS. The client rejects non-loopback HTTP and redirects.
Authentication uses **`Authorization: Bearer <key>`**, not `X-API-Key` (which the
production Gatekeeper ignores). Inject the key through your normal secret mechanism;
do not put it in a reference, source file or command transcript.

Gatekeeper services for uploading/deleting are:

| Service | Normalized authorization route | Methods |
|---|---|---|
| File Friend Uploads | `/files/` | POST, PATCH, HEAD, DELETE, OPTIONS |
| File Friend File Management | `/api/files/` | DELETE |

Administration is shared: a granted administrator can mutate any known upload,
regardless of its creator. Status and download reads use the secret capability
ID/URL and do not require that key. Possession of a reference is therefore sensitive;
avoid logging complete tool arguments, references or download URLs. File Friend
read access does not grant permission to send email; retain the email tool's normal
caller, account and recipient authorization.

## Producer: upload and pass the result

The `ff` command is a separate host application. Install it using the
[CLI installation guide](https://github.com/jmazzahacks/byteforge-mcp-file-friend-cli#install-on-a-host); installing SDK 0.0.3 or later does not install `ff`.

With `FF_TOKEN` already supplied securely:

```bash
export FF_SERVER_URL=https://ff.mazza.vc
umask 077
ff upload ./report.pdf --ttl 24h > report.reference.json
```

The file contains the **reference object itself**, not a status envelope. Progress
and errors go to stderr. Check the command exit code before passing its output.
Use the parsed JSON object as the receiving tool's argument, not the name of this
local JSON file.

Uploads default to 64 MiB chunks and a one-hour transfer deadline. The client hashes
the source and waits for server verification before returning. Rerunning an
interrupted upload with the same source and options resumes it; `--restart` starts
a new upload if the source/options changed. Increase `--timeout` for long transfers.

Python equivalent:

```python
import os
from pathlib import Path
from byteforge_mcp_file_friend_api_python import Client

client = Client("https://ff.mazza.vc", token=os.environ["FF_TOKEN"], timeout=3600)
reference = client.upload(Path("report.pdf"), ttl=86400).to_dict()
# Pass reference as structured JSON to the consuming tool.
```

### Reference contract

| Field | Type | Meaning |
|---|---|---|
| `id` | string | 32 lowercase hexadecimal characters |
| `url` | string | Absolute HTTPS download URL: `/f/<id>/<encoded filename>` |
| `filename` | string | Single filename, not a path |
| `sha256` | string | 64 hexadecimal characters, normalized to lowercase |
| `size` | integer | Exact file length in bytes, including zero for an empty file |
| `created_at` | integer | Creation time, Unix seconds |
| `expires_at` | integer | Expiry time, Unix seconds |

There is no MIME type, provider attachment ID, authentication token or trusted local
path in the reference. Forward the returned object unchanged. Do not synthesize a
reference from a tus upload URL: upload completion and verified publication are
separate stages.

Completed-file TTL defaults to 24 hours; accepted TTL values are 1–604800 seconds
(up to seven days). The completed TTL begins at publication. TTL is not a permanent
retention promise: administrators can delete earlier. Allow enough lifetime for
queueing, processing and retries. The consumer may reject an already expired
`expires_at` before fetching; the current helper does not itself enforce that time
field. Expiry cleanup is asynchronous, so do not rely on an exact-second HTTP cutoff.

## Receiver: resolve a reference

```python
from byteforge_mcp_file_friend_api_python import open_reference

with open_reference(
    reference,
    server_url="https://ff.mazza.vc",
    max_bytes=10 * 1024**2,  # Example consumer limit, not an email-provider guarantee.
    timeout=120,
) as path:
    process_file(path)  # Your function must finish using this path inside the context.
```

`open_reference` checks the reference identity, filename, origin, declared size and
SHA-256, downloads into a private temporary directory, and verifies the bytes before
yielding. It rejects redirects and other origins. Do not derive `server_url` from
caller input. If the consumer has an async MCP handler, run blocking resolution and
provider work through its existing thread/executor facility.

The helper's timeout bounds transfer/verification, **not arbitrary processing inside
the `with` block**. Set a separate provider/operation timeout. The default helper
limit is 50 GiB and its timeout is one hour; receiving tools should override both.

Do not retain or return the temporary path. Use the original reference's `filename`
for attachment naming. Optional `local_root` support exists only for tools already
sharing the explicitly configured trusted storage tree; remote email integration
should use ordinary HTTPS resolution, with no new shared mount.

## Email MCP: reviewed reply attachments

The email owner's [published reviewed-replies contract](https://github.com/jmazzahacks/byteforge-email-mcp/blob/22a0b1904b4e15fc35357222ee02e14b05797fed/docs/reviewed-replies.md)
is authoritative. File Friend supplies transport; the email service owns review,
MIME assembly, persistence, submission and provider limits.

1. Call `prepare_reply_draft(message_id, body_text, request_id, attachment_refs)`
   with an ordered list of complete reference objects. No base64, MIME hint,
   local path or upload token belongs in that list.
2. Review the returned sender, recipients, subject, full body and ordered
   attachment metadata with the owner. Persist approval of the exact fingerprint.
3. After that approval, call `send_reply_draft(draft_id, expected_fingerprint, request_id)`
   with a distinct submission request ID. Preserve it for retries/read-back.
4. Read `get_reply_draft_submission(draft_id)` for the outcome. An uncertain
   submission is not permission to send again or prepare a replacement draft.

Preparation creates a service-owned immutable snapshot; it does not send email or
create a provider draft. All calls must retain the same authenticated client identity.
Rivet enforces owner approval; a fingerprint is a content check, not consent.

Current service caps are 10 files, 10 MiB per file **and combined decoded bytes**,
a 120-second shared resolution deadline, and 20 MiB each for serialized MIME and
its Gmail base64url representation. Provider limits may be stricter. These caps
are independent of File Friend's 50 GiB default transfer limit.

The service validates all references and verifies all bytes before saving a draft.
The review snapshot contains ordered `{filename, content_type, size_bytes, sha256}`
entries. The email server infers MIME; unknown types and `message/*` use
`application/octet-stream` to preserve byte identity. Display that returned type.
Changing the body or attachments requires a new preparation key and new review.

Exact preparation replay can return an existing saved draft after File Friend
expiry/deletion without refetching. Changing any reference field or order under
the same key conflicts. The saved MIME freezes the bytes for later send and
reconciliation. Draft content expires seven days after preparation; receipts and
hashes remain. File Friend expiry does not recall delivered mail.

The resolver never deletes the producer's upload. The producer may remove it once
all consumers finish, or rely on TTL. Do not delete it automatically on receipt.

Email errors are sanitized: `attachment_invalid`, `attachment_limit`,
`attachment_expired`, `attachment_unavailable` (404/410), `attachment_download_failed`,
`attachment_configuration`, and `attachment_message_too_large`. Preserve the
email owner's submission/idempotency rules. Do not leak reference URLs or raw
network exception text through logs or tool errors.

For other consumers, use `open_reference` as shown above and keep processing
inside its context. Its timeout bounds transfer and verification, not arbitrary
provider work. Bound attachment counts and total bytes before fetching, resolve
all inputs before side effects, and choose operation-specific limits.

## HTTP contract and failure handling

Use the client/helper when possible. Non-Python consumers must implement equivalent
origin checks, bounded transfers and size/SHA-256 verification.

| Resource | Behavior |
|---|---|
| `POST /files/` | Authenticated tus creation; 201 with upload `Location` |
| `HEAD`, `PATCH /files/<id>` | Authenticated tus offset/resume; PATCH uses `application/offset+octet-stream` |
| `GET /api/files/<id>` | Public capability status envelope, typically 200 with `id`, `state`; `reference` only when ready |
| `GET`, `HEAD /f/<id>/<filename>` | Public capability download; Range supported; no caching |
| `DELETE /api/files/<id>` | Authenticated lifecycle deletion; normally 202 while cleanup proceeds, possibly 200 if already terminal |

Custom tus uploaders send `Tus-Resumable: 1.0.0`, exact `Upload-Length`, and
`Upload-Metadata` entries `filename`, `sha256`, `ttl` as unwrapped base64 values.
PATCH includes `Upload-Offset`; recover lost acknowledgements with HEAD. Use chunks
no larger than the client's 64 MiB maximum. After transfer, poll status; do not hand
off the tus Location as the download reference.

Status states include `uploading`, `verifying`, `publishing`, `ready`, `deleting`,
`deleted`, `expired`, and `failed`. Terminal failures may include an `error` code
(e.g. `hash_mismatch`). Retained deleted/expired rows return 410; unknown or removed
rows return 404. Static download URLs may simply return 404 after removal. The
Python client's status methods can raise `HTTPError` for 410; the CLI prints that
status and exits nonzero. Treat neither 404 nor 410 as a usable attachment.

| Failure | Receiving-tool behavior |
|---|---|
| Malformed reference, foreign origin, size limit, hash mismatch | Fail before sending; return a stable, sanitized attachment error |
| Expired/deleted/missing file | Ask producer for a fresh upload; do not send partial mail |
| Transfer timeout/transient network error | Bounded retry of resolution is acceptable before provider submission |
| Provider rejects message size/type | Return the provider rejection; do not silently replace attachments with links |
| Provider send outcome uncertain | Preserve uncertain status; reconcile before retrying send |

Do not forward exception text containing capability URLs into shared logs or tool
errors. Report attachment position, safe failure category and a correlation ID.
Consumer-facing error-code names should fit the receiving team's existing scheme.

## Pilot acceptance plan

No email should be sent as part of merely reading or adopting this guide. Run the
pilot only with express approval for the target production systems, activity,
account and recipient, beginning with service-owned reviewed drafts.

1. **Client baseline:** upload a small text file and a PDF to production, retain the
   returned references privately, and download each with the CLI/helper. Compare
   SHA-256 and size with the original files.
2. **Draft integration:** call `prepare_reply_draft` with both references.
   Verify the service-owned review snapshot and saved MIME contain both attachments,
   original filenames and byte-identical payloads. Confirm body/recipients remain correct. Calls without attachments must behave as before.
3. **Failure atomicity:** malformed/foreign-origin references, invalid hashes,
   oversized/count/combined limits, and expired/deleted references must produce no
   provider submission. With a valid first attachment and failing second attachment,
   verify no partial draft/send and no leftover temporary files.
4. **Approved send:** send a small attachment message to the agreed test mailbox.
   Verify received attachment bytes, not just a provider success response. Cover
   each supported provider before claiming cross-provider support.
5. **Lifetime/retry:** demonstrate a saved reviewed draft retains attachments after the
   source File Friend upload is deleted. Exercise provider rejection and ambiguous
   send outcome with mocks; verify no blind resend. Test temp cleanup on exceptions.
6. **Resume and realistic size:** interrupt/retry a moderately sized upload and
   download; check Range/resume and integrity. Measure memory, disk use and elapsed
   time at the consumer's supported email attachment limits. Large File Friend
   transfer benchmarking is a separate test, not an email-provider capability.
7. **Observability:** confirm useful counts/outcomes without keys, references,
   filenames, message contents or capability URLs in logs/metric labels. Clean up
   pilot drafts/messages only in the approved test account; delete synthetic uploads
   through `ff rm` when no consumer needs them.

Useful CLI checks (values below refer to the uploaded file's ID or download URL):

```bash
ff info '<id-or-download-url>'
ff download '<id-or-download-url>' -o ./received-report.pdf
ff rm '<id-or-download-url>'
```

Do not use a status endpoint URL as the CLI argument. `ff rm` acknowledges deletion
intent; poll status for cleanup completion if the test requires it.

## Ownership and handoff checklist

- File Friend team owns the CLI, upload/reference contract, Python SDK and helper.
- Email team owns MCP schema changes, resolution adapter, provider limits, MIME
  assembly integration, errors and email tests. Other consumers own their adapters.
- Host/database/monitoring changes go to Apollo, Athena and Janus respectively.
  No team should edit another owner's repositories or infrastructure.

For reviewed replies, use the existing email contract above. New compose or other
consumer integrations must separately define their parameter shape, limits, MIME
policy, error mapping and approved pilot. No File Friend database access, upload
key for read-only consumers, or shared filesystem mount is required.
