# Proof and verification

I keep implementation evidence separate from benchmark conclusions. Tests show that a defined behaviour held under their fixtures; benchmarks compare measured outcomes on a stated workload. Neither one proves every future input will work.

## Export integrity

`MANIFEST.sha256` contains a SHA-256 checksum for every exported file except the manifest itself. I regenerate it after each change and verify the complete list. The finished export is also scanned for credentials and personal data. Synthetic scanner fixtures and model identifiers are reviewed as narrow, documented exceptions; credentials and private runtime records are not included.

The scanner's fake-secret test fixtures are not published here because they look like real tokens to hosting providers.

## Cleaner safety suite

The retained cleaner report records a final result of 28/28 passing tests. The tests use temporary directory trees and fake command results instead of live cleanup paths, process gates, file-holder tools, Windows temporary files, or the self-improvement journal.

The suite covers protected paths, approvals, a busy-state refusal, live file handles, incomplete process visibility, changing files, links, repository markers, and audit failures. Command help, version output, and whitespace checks also passed. This evidence supports the conservative implementation, but it does not remove the documented race between a final live-use check and deletion.

## Local task-driver checks

The typed-check report records 14 model-free unit tests passing, both original unsafe-patch rejection checks passing, Python compilation succeeding, and the shared helper passing strict Pyright with zero errors or warnings. A deliberately weak test accepted `5.0 == 5`, while the checker rejected the injected `-> int` contract violation; that was a synthetic regression test, not a discovered production bug.

The real-model A/B result is kept separate: it found no checker-only model bug and a lower acceptance rate with checking enabled. That is why checking remains opt-in even though the checker integration itself passed its regression tests.

## Frozen self-improvement benchmark

The shipped benchmark definition contains 30 synthetic cases and 90 assertions. Its policy tests copy the approved settings and benchmark into temporary directories, validate all 90 assertions with independent reference functions, and exercise approved setting steps, boundary refusal, input hashing, and rollback.

The controller does not execute generated source by itself. A separately reviewed runner supplies measurements. A content hash proves that the listed inputs stayed byte-identical; it does not prove that the runner was honest or that every external dependency was listed.

## Firstmate evidence boundary

The local Firstmate branch includes regression coverage for session membership, lock ancestry, startup continuity, watcher recovery, and supervision handoffs. I describe those test surfaces without claiming a fresh full-suite result in this export. Firstmate's source and validation runtime remain in their own checkout rather than being copied here.
