# Latest updates

## 2026-11-10: six-list task view and weekly fallback

The shared task-report format was extended to six lists: Queued, Self-improvement loop, Job Pilot, Idle guard, Things that need me, and Remind me. The rules keep completed work out of the live view and preserve a manual Sunday-maintenance path when the normal maintainer is unavailable.

## 2026-11-07: cleaner shared-session view

The latest local Firstmate commit hides ended session members from the default listing. This builds on the local shared-home membership work, making the normal view focus on sessions that are still active without deleting the historical records.

## 2026-11-03: related-idea search

The self-improvement knowledge graph gained vector-based related hints for differently worded ideas. Exact matching still runs first. Similarity is only a non-blocking hint, so a weak semantic match cannot prevent a proposal or make a decision automatically. The feature remains under trial measurement.

## 2026-11-02: background-app review

The weekly review now includes a sweep of uncovered Windows autostarts, store applications, and non-Microsoft services. The implementation reports user-level candidates and separates risky machine-wide, hardware, networking, service, and security items for manual review. It does not disable anything automatically.

## 2026-10-31: faster production model profile and model watch

The local Qwen server moved to the measured 24-layer n-gram profile, with the 14-layer and CPU profiles retained as startup fallbacks. The weekly model comparison also adopted a conservative decision rule: a replacement must improve correctness without slowing any measured item, otherwise it can only be considered as a fallback.

## 2026-10-28: conservative cleaner and optional type checking

The local cleaner was completed with report-only defaults and a single protected Go build-cache deletion rule. The task driver gained optional Python type checking and detailed per-generation measurements. Its first paired comparison did not establish a reliability gain, so checking stayed off by default.

These notes describe changes supported by the dated local charter, reports, runtime profile, and Git history. Experimental or proposed work is labelled as such rather than presented as finished behaviour.
