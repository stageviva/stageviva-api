# AI request accounting

The emergency enable switch, gpt-4o-mini model allowlist and configured request limit are unchanged. `reserve_ai_request` must finish before a provider call is sent. Failed provider operations still consume a reservation.

## Persistent state

`STAGEVIVA_AI_USAGE_STATE_PATH` retains its existing JSON path/format. A stable sibling `.lock` file coordinates processes and records that the ledger was initialized. Retain both files on the same persistent disk; never rotate/delete the lock while workers run. Valid legacy JSON is accepted without resetting its count. No database migration or new dependency is required.

Reservations use an OS file lock (Windows byte-range lock or POSIX flock), validate the ledger, write through a unique temporary file, fsync it, and atomically replace the JSON. A valid previous UTC day resets normally. Corrupt, inconsistent, unreadable, future-dated or missing previously initialized accounting blocks AI. Invalid/zero limits also block. Lock contention times out after five seconds.

If accounting blocks unexpectedly, leave AI disabled while investigating. Preserve the files and verify the disk mount, permissions and actual usage. Do not restore a fresh zero count just to resume service. Manual repair must preserve a conservative count and be explicitly approved for production. Deleting both files can bypass initialization history and is not an appropriate recovery method.

## Limits of the protection

- This counts logical provider operations, not money. SDK retries can make additional HTTP attempts within one reservation; existing bounded SDK retry settings have not changed. This work does not establish a dollar cutoff.
- Cooperating workers must share the same persistent path and filesystem lock semantics. Separate hosts/disks have independent allowances. Before deployment, verify actual topology; distributed accounting needs a shared transactional service.
- Windows concurrency was tested locally. The POSIX branch and production filesystem need Linux/staging verification. Power-loss guarantees depend on the filesystem; backups/restores must preserve usage conservatively.

## Offline verification

Local runner: `C:\Dev\StageViva\migration-notes\offline-safety-cv-checks.py`, invoked with backend virtualenv Python and `-I`. It clears inherited environment, disables dotenv/AI/scans/Stripe, blocks external network, uses temporary synthetic storage and does not enter app lifespan. Windows asyncio's private loopback socket pair is constructed explicitly; it cannot connect externally.

Tests cover corrupt/inconsistent/missing state, read/write failure, UTC rollover, restart persistence and 12 competing interpreters sharing a four-request limit. CV tests cover eight competing database connections, repeated HTTP submissions, save failure, completed-job replacement and unchanged monthly allowance. Actual analysis/OCR/provider calls are mocked or never invoked.
