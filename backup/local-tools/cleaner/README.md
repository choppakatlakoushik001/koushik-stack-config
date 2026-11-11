# cleaner

A conservative Python 3 cleaner for this Linux/WSL machine. It uses the standard
library, runs only when asked, and never kills a process. `scan` is the default
and does not write approvals, audit records, or cleanup files.

```sh
python3 clean.py                 # report only
python3 clean.py scan --full     # every candidate, rather than the first five
python3 clean.py approve go-build
python3 clean.py apply           # permanent deletion; no trash or undo
python3 -m unittest discover -v
```

Reports use a small TOON table: each category has measured allocated bytes, eligible
bytes, an approval flag and a plain-English explanation. Paths are quoted; process
command-line arguments and file contents are never printed. `--help` is available
on every command; unknown arguments fail before scanning. Python 3.10+ on Linux is
required. No package installation or session hooks are needed.

## Category policy

Only **go-build** supports deletion in this version. Its fixed root is
`~/.cache/go-build`; `go env GOCACHE` must confirm that exact location. Only files
in Go's two-level hexadecimal cache-entry layout are candidates. Go can regenerate
these entries from retained source; later builds can be slower. Cache directories,
README files, trim metadata and unrecognized entries are retained.

The other categories are deliberately **report-only and cannot be approved**:

- **cuda-toolkit:** measure `~/local-llm/tools/cuda-13.1`; inspect llama.cpp CMake
  caches, `ldd` for existing llama-server/cli/bench ELF binaries, PATH,
  LD_LIBRARY_PATH and a bounded set of local script directories. A positive
  reference requires retention. Even zero hits cannot prove absence of other
  references or future use, so no toolkit deletion is implemented.
- **bench-logs:** measure `~/local-llm/bench-logs`. A report existing beside a log
  does not prove every raw result was summarized. No logs, reports, summaries or
  Python files are removed without a future verifiable summary manifest rule.
- **firstmate-temp:** measure only direct `.claude-autoarm-output.<alphanumeric>`
  files in the Firstmate state directory, owned by this user and older than seven
  days by both modification and metadata-change time. The producer uses a random
  suffix, so there is no reliable owner PID or job-completion proof. No other
  Firstmate files are traversed.
- **tmp:** measure only direct, user-owned, empty regular files in `/tmp`, older
  than seven days by both timestamps. No recursion or deletion: an empty old file
  may still be a marker needed by a future job. Nonempty abandoned files have no
  reliable purpose/owner proof and are excluded.

This intentionally drops deletion rules whose disuse cannot be demonstrated.
A category approval never overrides a protection or enables a report-only rule.

## Approval and deletion checks

`approve go-build` shows the full current list and records the explicit command's
category-wide approval in `approved.json` beside `clean.py`. This file is private
(mode 0600) and excluded from Git. Approval persists for future matching Go cache
entries; a new scan is always performed at apply time. Scan alone never creates
this file. No categories are approved by default. Repeated approval is a no-op.

`apply` requires `jp-gate` to exit successfully with exactly `idle` on stdout and
no diagnostics. It rechecks the gate before each unlink, refuses overlapping
cleaner mutations with a file lock, and for each approved candidate:

1. Checks the exact root, Go entry name, user ownership, single hard link and
   regular-file type. Symlinks anywhere in the path are refused.
2. Refuses Python-related paths, caches, environments (including `pyvenv.cfg` and
   conda markers), site-packages, models/Qwen, Job Pilot personal data,
   ms-playwright, `.treehouse`, Git repositories/worktrees/bare repositories,
   Firstmate state, and every location outside the category root.
3. Reads `/proc` file descriptors, mapped libraries, cwd/executable paths and
   command-line references. Unreadable processes whose UID differs from the
   cache target owner's UID do not block deletion: other-user system processes
   cannot access standard user-owned cache files (mode 0700/0600) without elevated
   privileges, and their inability to be ptraced or inspected should not freeze
   cleanup of user caches. For same-UID unreadable processes (such as non-dumpable
   credential programs or PAM-started per-user managers), only an explicit allowlist
   of known authentication/credential daemons and session services (`ssh-agent`,
   `gpg-agent`, `gnome-keyring-daemon`, `keyboxd`, `secret-tool`, `systemd`, `(sd-pam)`)
   is permitted; any other unreadable process belonging to the same UID counts as
   incomplete process visibility and refuses deletion. Names come from `/proc/<pid>/status`
   and a same-UID program could rename itself, so this allowlist is an operational
   convenience rather than a cryptographic security boundary. Active Go producers
   or readable processes referencing the cache also block deletion.
4. Requires **both** `lsof` and `fuser` to report no holder. Missing commands,
   permission warnings, unexpected output or errors refuse deletion.
5. Rechecks protections and device/inode/type/owner/link-count/size/timestamps.
   It opens each parent directory without following links and unlinks the basename
   through the pinned parent descriptor. Directories are never deleted.

Every apply attempt, including gate refusals, invokes
`python3 ~/local-llm/selfimprove/si.py record --task cleaner ...`. This appends the
normal JSON line to `selfimprove/records.jsonl`; `--checks` records deleted files,
safety refusals and estimated freed allocated bytes. Audit failure is explicit.
Partial deletion is possible before a later refusal; there is no rollback.

An old timestamp says when contents last changed, not when someone will next need
them. Regenerability says a build can recreate a cache, not that nobody uses it
now. Those facts are why we use a narrow cache rule, approval and live-use checks
rather than broad age-based cleanup. Approval and repeated checks reduce races;
they cannot freeze unrelated programs. Another process could open or replace a
file immediately after the last check. This tool does **not** claim mathematically
atomic disuse proof or protection from a malicious concurrent filesystem writer.
Use a genuinely idle machine. If that remaining race is unacceptable, keep using
`scan`. Disk bytes are allocated-block estimates; filesystem compression, reflinks
and delayed reclamation can make physical free-space gains differ.

## RAM and Windows

The report shows the five largest readable processes by RSS. A possible orphan is
adopted by PID 1 (or has a missing parent), has no tty, is over an hour old and has
lifetime CPU below 0.1%. These are hints, not proof: ordinary daemons fit this
pattern. Nothing is killed, signalled, dropped from RAM or written to swap.

Windows reporting uses read-only PowerShell enumeration of process `%TEMP%` and
sums logical file sizes. Missing PowerShell, permission failures or timeouts are
reported as unavailable. No Windows deletion or scheduling is implemented.

## Tests and development

Tests use temporary trees and mocked command results, never real cleanup paths,
`jp-gate`, `lsof`, `fuser`, PowerShell or the audit writer. They exercise actual
unlinks only within their temporary fixtures. The safety suite covers protected
paths even with a matching rule, approvals, busy gates, live handles, incomplete
visibility, file changes, links, repository markers and audit errors.

The AXI SDK in the workspace is JavaScript-only; adding a Node runtime to a single
Python script would add unnecessary dependencies. This script implements only the
small flat-table TOON output it needs, with JSON-escaped cells.
