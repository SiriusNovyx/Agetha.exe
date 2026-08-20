# Fast Mode security and recovery

Fast Mode temporarily manages a fixed, non-secret configuration allowlist. Its
transaction state is exactly:

```text
config.txt
memory/fast_mode_snapshot.json
memory/.fast_mode.lock
```

## Threat model and limits

The transaction layer is designed to resist accidental corruption, concurrent
Agetha/Medic updates, malformed snapshots, partial activation or restoration,
path redirection, and practical symlink or Windows reparse-point substitution.
It also fails closed if a future edit adds a secret, provider, permission,
confirmation, protected-process, OCR-privacy, remote-OCR, or consent setting to
the Fast Mode profile.

`COMPACT_MODE` is specifically outside the Fast Mode allowlist. Fast Mode must
not turn Compact off, restore an old Full/Compact value, or stand in for the
deliberate Full-consent flow. Compact is the outer advanced-capability gate;
Full still retains the guard/confirmation/security settings that Fast Mode is
already forbidden to weaken. See [Compact and Full profiles](compact_full_mode.md).

Fast Mode is not a privilege boundary. A fully compromised process running as
the same user can still interfere with files owned by that user. Descriptor
checks, post-lock revalidation, and atomic replacement narrow practical race
windows; they do not mathematically eliminate every possible same-user attack.
Use a per-user installation directory that is not writable by untrusted users.

The snapshot contains only approved setting names, presence flags, original
single-line textual values, forced values, and optional restore preferences.
It must never contain provider/API keys, OCR or screenshot contents,
conversations, documents, full config text, or memory contents.

## Locking and path validation

All mutating operations serialize through `memory/.fast_mode.lock`. An existing
unlocked lock file is normal and is reused. Acquisition retries every 75 ms for
at most four seconds; timeout returns `profile_busy` without modifying the
config or snapshot.

On POSIX, the lock is opened with `O_NOFOLLOW` where available, checked with
`fstat()` and `lstat()`, required to be a regular file, and matched by device and
inode before `fcntl.flock()` is used. On Windows, a standard-library `ctypes`
`CreateFileW` open uses `FILE_FLAG_OPEN_REPARSE_POINT`; reparse points,
directories, non-disk handles, and path/handle identity mismatches are rejected
before the handle is converted for `msvcrt` byte-range locking. No `pywin32`
dependency is required.

Immediately after acquiring the OS lock, the implementation revalidates the
config, memory directory, snapshot, and lock descriptor/path identity. A failed
check releases the lock and returns `unsafe_path_state` before a transaction
write begins.

## Atomic writes and Windows ACLs

Config and snapshot writes use unpredictable, exclusively created temporary
files in the destination directory. They are flushed and file-fsynced before
`os.replace()`; the containing directory is fsynced on POSIX. Pre-replacement
failures clean the temporary file and report `write_not_applied`. A failure
after replacement reports `write_applied_verification_failed` so callers do not
pretend the old state still exists.

POSIX files are restricted to user read/write permissions. On Windows,
`chmod(0o600)` does not create a POSIX-style ACL: files inherit ACLs from the
per-user application directory. The snapshot intentionally stores no secrets.
Explicit ACL tightening would require native ACL APIs and is not performed in
normal startup.

## Ambiguous writes and recovery

If replacement may have completed but the verification read, validation, cache
update, or directory durability step is interrupted, the public status is
`verification_pending`. In-memory profile caches are invalidated and recovery
metadata is retained. The next reconciliation inspects disk truth; it does not
trust cached state, invent new originals, replay a completed restoration, or
reactivate a cleanup-only snapshot.

Windows operators can use Medic Checker's separately confirmed status,
reconcile, and restore actions. Linux and direct-Python operators can run:

```bash
python -m agetha.core.fast_mode_profile status
python -m agetha.core.fast_mode_profile reconcile
python -m agetha.core.fast_mode_profile restore
```

`status` is read-only. Exit codes are:

| Code | Meaning |
|---:|---|
| `0` | Healthy state or requested operation completed |
| `1` | Recoverable action is required |
| `2` | Invalid/unsafe state or operation failure |
| `3` | Another process still owns the profile lock |

For `profile_busy`, close the other Agetha or Medic instance and retry. For
`verification_pending`, run reconciliation again first. Do not delete the
snapshot manually unless its contents and current config state have been
independently reviewed.

## Audit events

Transitions append best-effort structured records to
`memory/audit_log.jsonl`, including activation start/success/failure, repair,
restoration start/success/conflict, cleanup pending/completed, migration, busy,
verification pending, invalid snapshot, and quarantine. Records contain only
status, schema/profile versions, platform, counts, and changed/conflict key
names. They exclude values, secrets, paths, config/snapshot text, OCR,
conversations, and documents. Audit failure never blocks recovery.

## Validation coverage

`tests.test_fast_mode_security` covers the forbidden-profile invariant,
strict snapshot allowlisting, no-follow lock opening, descriptor identity,
bounded locking, injected post-lock path replacement, atomic-write failure
states, disk-truth recovery, audit redaction, and CLI behavior. Actual POSIX
symlinks run on Linux. Windows uses actual symlinks where runner privileges
permit and always retains mocked reparse-point coverage.

The GitHub Actions matrix runs Python 3.13 on `windows-latest` and
`ubuntu-latest`. Hosted Windows CI is x64; Windows ARM64/Snapdragon under Prism
must be validated manually on suitable hardware.
