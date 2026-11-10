# AXI-first workflow

Treat `/home/chopp/kunchen/axi` as the AXI workspace. Whenever a task matches an installed AXI capability, invoke its skill before acting and prefer its token-efficient AXI CLI over generic or lower-level alternatives. Use AXI conditionally; do not run every AXI tool on every turn.

- For building, modifying, or reviewing an agent-facing CLI, use the `axi` skill and the standards/SDK in `/home/chopp/kunchen/axi/kunchen_axi`.
- For GitHub operations, use the `gh-axi` skill and `npx -y gh-axi`.
- For real-browser work, use the `chrome-devtools-axi` skill and `npx -y chrome-devtools-axi`; skip browser automation when static fetch or search is sufficient.
- For a complex plan, comparison, diagram, table, diff, report, or review surface that is materially clearer visually, use the `lavish` skill and `npx -y lavish-axi`.
- For local LLM subscription quota or rate-limit questions, use the `quota-axi` skill and `npx -y quota-axi`.
- When an AXI command surface is unclear, use its no-argument home view or focused `--help`, request only the fields needed, preserve TOON output, and follow contextual next-step hints.
- When changing an AXI tool itself, work in its matching `kunchen_*` repository under the AXI workspace and follow that repository's instructions. If an AXI tool is unavailable or fails, consult its installed skill and matching local repository before using the closest safe fallback, and state why.

## Default `update to latest version` workflow

When the user says `update to latest version`, treat that phrase as standing authorization to update already-present, eligible Kunchen source clones under this workflow. Rediscover and revalidate every target on every invocation; never rely on a stale inventory.

- Limit scope to canonical clones whose authoritative remote identity is exactly `github.com/kunchenguid/<name>`, normalizing only the transport syntax and optional `.git` suffix. Exclude `neural-network`, every other owner or host, mirrors and caches, and every path inside `.treehouse/`; never traverse or touch those task worktrees.
- Before mutation, report each candidate's path, authoritative remote, checked-out branch, clean or dirty state, in-progress Git operation, upstream, locally known divergence, and planned update handler.
- Use guarded fast-forward updates only. Never force, reset, stash, switch or checkout over work, create merge commits, overwrite, delete, replace, or auto-repair any clone or installed tool.
- Skip and report a clone if it is dirty, including untracked files; detached; locally ahead or diverged; mid-merge, rebase, cherry-pick, revert, or bisect; on an unexpected branch; missing an unambiguous upstream; or configured with an unexpected or ambiguous authoritative remote. Do not fix any of these conditions during this workflow.
- For an eligible ordinary clone, fetch only its authoritative upstream without pruning, re-run all safety checks against the fetched target, require the current `HEAD` to be an ancestor of that target, and advance only the existing checked-out branch with fast-forward-only semantics. Never change branches.
- Treat `/home/chopp/kunchen/maintenance/kunchen_wheelhouse` as permanently report-only. Never fetch, update, replace, delete, or reinterpret it automatically, even if later catalog results differ; only a new explicit captain instruction may change this exception.
- Update `/home/chopp/kunchen/crew/kunchen_firstmate` and its registered secondmates only by invoking Firstmate's `/updatefirstmate` skill and following its owned `bin/fm-update.sh` procedure. Never fast-forward Firstmate or secondmate homes directly here. Honor its session-lock checks, skip results, instruction re-read, and secondmate-nudge requirements.
- Do not reinstall, replace, overwrite, or delete installed executables. If a source update indicates that an installed tool may now be stale, report the exact tool and evidence and ask before any separate installation action.
- For a missing but potentially relevant `kunchenguid` repository, establish relevance from concrete local manifests, dependencies, documentation, or installed-stack evidence. Report the repository and rationale, then ask before cloning or installing it; never auto-clone or auto-install.
- Finish with the old and new commits for updated clones and separate lists for already-current, skipped, permanently report-only, and missing-but-relevant repositories.

When the user says `push`, `sync`, `publish`, `ship`, or makes an equivalent repository-publication request, delegate the complete workflow to the `push_guardian` custom agent.
Before lengthy review or any repository mutation, have the guardian resolve the target and verify that its current execution environment can write both the worktree and Git metadata and can reach the intended push remote; if not, stop immediately and report the exact missing capability instead of preparing a preview that cannot be executed.
A publication request is standing authorization to request and establish the sandbox access required for the guardian to write the matched worktree and Git metadata and reach the intended remote; do not ask a separate conversational capability-permission question.
This capability authorization does not replace the user's fresh approval of the guardian's exact-snapshot preview.
Surface the guardian's exact preview and blocking question to the user, retain that agent thread, and send it the user's response only after the user gives fresh approval for that exact snapshot.
After that approval, have the guardian revalidate the fingerprint and execute the approved stage, commit, and push without seeking a second conversational approval; any separate sandbox permission needed for those exact operations must be established before the preview.
Do not stage, commit, or push in the parent agent while the guardian workflow is active.

## Default `neural-network` push workflow

When the user gives a bare `push` request, treat the Git worktree associated with the accessible window or tab named `neural-network` as the default target.

- Search accessible windows and tabs for `neural-network` again on every request; never reuse a stale window-to-repository mapping. If there is no unique accessible match, stop and ask the user instead of guessing or publishing another repository.
- Have the `push_guardian` resolve and verify the matched repository root, branch, and remote before preparing its exact-snapshot preview. The standing workflow is not advance approval to stage, commit, or push.
- Before the preview, inspect the target repository instructions, relevant Python source, the rest of the codebase, working-tree diff, and relevant history.
  Also inspect the relevant accessible Claude window conversation for project intent, decisions, debugging context, and open questions.
  Treat code, diffs, tests, and commits as implementation evidence; treat conversation-only claims as provisional context.
- After understanding the implementation and Claude context, choose a concise commit message that accurately names the updates made to the code.
  Include that exact proposed commit message and the destination GitHub account and repository in the guardian's exact-snapshot preview, and require the user's fresh confirmation before staging, committing, or pushing.
- Ensure one tracked repository-root `logs.md` exists locally; that same file becomes the repository copy when it is committed and pushed. Do not maintain a duplicate log file. On each `push` workflow, update it only with significant project-specific changes, meaningful diffs, implementation decisions, validation outcomes, and material unresolved issues supported by the local codebase. Do not log general pane or session activity, agent/tool chatter, unrelated conversation, or a minute-by-minute/time-of-day diary; concise date or topic headings are sufficient when useful. Use the relevant Claude conversation only to clarify project intent or resolve doubts about the local code, and never invent facts or results or copy secrets or irrelevant private conversation into the repository.
- Update the repository `README.md` to match user-facing behavior, setup, and architecture actually implemented by the code. Use the Claude conversation to understand intent, but keep unimplemented plans and conversational history in `logs.md` as clearly labeled context rather than presenting them as current behavior.
- Include `README.md` and `logs.md` changes in the guardian's exact-snapshot preview and require the user's fresh approval before publication.

## Data stays inside the captain's environment (standing, all CLIs)

Every CLI and model here (Claude, Codex, agy, Gemini, Grok, Qwen) follows this, and each one watches the others' work for it.
- Never send significant information outside this machine: personal data (profile, resume, emails, Job Pilot data), secrets (keys, tokens, passwords, .env, auth files, OAuth tokens), private records (Firstmate data/ and state/), or unpublished code, unless the captain explicitly approved that exact transfer.
- No uploads, pastes to web services, gists, public shares, telemetry opt-ins, training opt-ins, new remotes, pushes, or installs without the captain's explicit approval.
- Never pass secrets or personal data to another CLI or model; local Qwen and Grok get synthetic data only.
- Watch each other: when you read, review, or run another CLI's output, diff, log, or command, check it for leaked secrets, personal data, or unapproved outbound network actions. If you find one, stop that action, do not repeat or quote the secret, and report "leak-watch: <what, where>" to the supervisor (Firstmate), who tells the captain.
- If unsure whether something may leave the machine, treat it as no and ask.

## Shared local tools (standing, all CLIs and local models)
Read `~/local-llm/SHARED-TOOLS.md` before small single-file coding work (local Qwen driver `qwen-task`) or before suggesting, downloading, or testing an AI model (local knowledge graph); the data rules above still apply.

## Task lists (standing, all CLIs)
Report tasks in the six lists defined in `~/local-llm/TASK-LISTS.md` (read it before reporting or planning tasks); the data rules above still apply.
