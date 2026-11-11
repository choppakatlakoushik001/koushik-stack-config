# Task lists (standing rules for every AI CLI on this laptop)

Captain's standing instruction (2026-11-08, extended 2026-11-10): every task report, in every CLI, uses these six lists in this order.
The data rules in your global instruction file still apply in full; nothing here permits sending anything off this machine.

## The six lists

Show only live tasks (running, queued, or still needed). Finished work leaves the lists.

1. Queued - ordinary work. Runs first, strictly one by one in order, without waiting for a prompt.
2. Self-improvement loop - weekly review, proposals, tests of adopted changes. On Sundays (loop day) Queued pauses: run and test all of that day's self-improvement tasks, and put their follow-ups at the top of Queued.
3. Job Pilot - development, testing and maintenance of the job-search tool. On weekdays when Job Pilot is being developed or run, its development and testing come after the self-improvement tasks are run, tested and queued, then complete in order. Weekday maintenance runs in polite mode (low priority, memory caps, back-off).
4. Idle guard - heavy compute (local GPU jobs, model tests, long batches). Runs only while the laptop is locked or the captain is idle; stops at once when the captain returns. Light cloud labour may run while the captain is active.
5. Things that need me - approvals, merge words, decisions, first-principles explanations, notifications, landings. Handled when convenient for the captain; gates only the specific task it names.
6. Remind me - anything the captain asked to be brought back later, each with its trigger (a date, a time, or a situation).

Never wait: run Queued automatically first and Idle guard next, except where Sunday's loop or Job Pilot work has priority under the order above.

## Every report

- An explain part: what was done, why that way (and what was rejected), how, and the real results, from first principles, defining each term.
- One explicit completion line per job: "complete, waiting for your merge", "landed", or "not finished: <reason>".

## Where the real data lives (nothing is copied; the lists are only a view)

- The Firstmate backlog is the one record of work items (Queued, Job Pilot, Self-improvement, Idle guard, and held items for lists 5 and 6). Firstmate (the supervisor) renders the full lists.
- The weekly loop's own records live under `~/local-llm/selfimprove/`.
- This is private setup history. Cloud-model CLIs (Claude, Codex, agy, Gemini) must not read it into their prompts beyond what the captain asked about in this session, and must never send it off the machine. Local Qwen and Grok get synthetic data only.

## What a CLI that is not Firstmate does

- Report your own tasks in this six-list format, with the explain part and completion lines.
- To add, finish or move a task in the shared backlog, tell the captain, or ask Firstmate to record it; do not edit Firstmate's data files yourself.
- Cloud-hosted web assistants cannot reach this laptop and are outside this system (strict local-only).

## If Claude is out of limit

The Sunday loop and Firstmate duties have a manual fallback: see `~/local-llm/selfimprove/FALLBACK-MAINTAINER.md`.
