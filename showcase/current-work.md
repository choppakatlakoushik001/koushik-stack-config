# Current work

## Complete the frozen local-model baseline

The next self-improvement milestone is a complete run of the 30-case frozen benchmark while the laptop is idle. The latest weekly summary had no complete baseline for the two approved tuning settings, so the controller correctly skipped both changes. I need all original cases, valid timing and outcome measurements, and the pinned content hash before a trial can start.

## Route small local-model jobs more carefully

The latest real-job summary showed 3 passes in 7 jobs below 1,000 prompt tokens and 2 passes in 52 jobs at or above 1,000 tokens. That is operational evidence that job size matters, but it is not yet a controlled benchmark and does not justify an automatic setting change by itself.

The current direction is to keep jobs narrow, measure the frozen cases, move the approved split threshold only within its bounds, and send unsuitable work to a stronger fallback. The proving condition is higher pass rate without increasing median minutes per original case.

## Keep type checking optional until it earns a default

The first paired driver comparison found fewer accepted runs with type checking enabled and no model bug caught only by the checker. The integration remains available for tasks where annotations add useful evidence, but it will not become the default without a larger representative comparison showing a reliability gain.

## Reconcile the local Firstmate branch safely

My Firstmate checkout contains the local multi-session, lock, watcher, and startup-continuity work described in this repository. Git shows 26 patch-unique non-merge commits plus one local merge commit beyond the tracked upstream branch; none of the commits supporting the write-up is contained in a tracked remote branch. The checkout also has newer upstream work to reconcile, so updates must use Firstmate's own guarded update path rather than overwriting the local branch.

## Keep the backup current

This repository is a checksum-backed snapshot of the configuration and selected local tooling I use. I will refresh the exported files and showcase notes when the setup changes, then regenerate the manifest so each snapshot stays traceable.
