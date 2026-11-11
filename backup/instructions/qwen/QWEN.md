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
