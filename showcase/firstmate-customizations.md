# Firstmate customizations

I run [kunchenguid/firstmate](https://github.com/kunchenguid/firstmate) as the supervision layer for this workspace. The upstream repository remains the source of the project. I do not copy its source into this backup; this note records the areas changed on my local branch.

## Shared session membership

My local branch changes a shared Firstmate home from a single-session assumption to explicit session membership. Multiple command-line primaries can join the same home without silently replacing one another. Member identity is preserved across startup, trace propagation, supervision branches, and watcher ownership. Ended members are hidden from the default session list so the normal view shows the sessions that still matter.

## Safer lock and takeover behaviour

The local changes add checks for stale, chained, and displaced session-lock holders. Takeover is bounded so an idle former primary can be superseded without wedging the home, while a live supervision branch is not mistaken for a displaced process. A checkpoint releases only the watcher it owns when it times out.

## Watcher continuity

The Stop-hook and watcher work is designed to preserve a wake through refused or repeated handoffs. Shared watcher beats can satisfy joined sessions, branch generations stay isolated, and repeated re-parking is bounded. The goal is for one healthy supervision cycle to remain available without creating duplicate cycles or losing a pending wake.

## Startup and update behaviour

The local branch preserves per-member startup and trace identity across the supported command-line surfaces. It also changes local Firstmate updates to wait for nighttime inactivity, so an update does not replace a running workspace in the middle of active work.

## Regression coverage

The same branch adds or extends tests for session membership, session phases, continuity across restarts, lock ancestry, live lock handling, watcher recovery, Stop-hook behaviour, startup, trace context, and supervision-host interactions. These tests stay with the Firstmate checkout; this backup includes only this high-level description.

## Private boundary

Firstmate's `data/`, `state/`, `config/`, `projects/`, and validation runtime are deliberately excluded. They contain local records and operating state, not material for a configuration showcase. Upstream code remains linked rather than republished.
