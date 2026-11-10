# Shared local tools (every AI CLI and local model on this laptop)

Captain's standing instruction (2026-11-01): share these with all current and future AI CLIs and local models only.
The data rules in your global instruction file still apply in full; nothing here permits sending anything off this machine.

## 1. Qwen driver: `qwen-task SPEC.json`

Runs one narrow, single-file coding job on the local Qwen model, in an isolated copy; it never changes the source repo, it writes a result folder with the patch and a verdict.
Prefer it for small single-file work before spending cloud tokens.
How to write a spec: `~/local-llm/driver/README.md` and `qwen-task --help`.
Inputs must be synthetic or plain code only: no secrets, personal data, Job Pilot data, or Firstmate `data/`/`state/` content.

## 2. Knowledge graph: what this setup already learned about AI models

`python3 ~/local-llm/selfimprove/si.py graph query <model-name> -r <relation>`

Names are normalized, so `Qwen3-4B` and `qwen3-4b` match.
Check it before suggesting, downloading, or testing a model: do not re-propose a model whose `declined` is true or whose `status` is `tried`.

- Allowed relations for every CLI (public model facts): `declined`, `status`, `verdict`, `tested`, `publisher`, `size_gb`, `fits_threshold`, `type`, `best_candidate`.
- Everything else in the graph (task records, proposals, decisions, concepts) is private setup history: cloud CLIs must not query it or send it to their model; only Firstmate uses it.

## 3. Shared lessons (how the captain wants every AI to work)

Curated from what this setup learned; Firstmate's weekly self-improvement loop adds new general lessons here. Private history stays out.

- Explain what you did, why that way (and what you rejected), how, and the real results, from first principles and beginner-level: define each technical term the first time it appears.
- Write formulas as plain text (e.g. `p^N`), never LaTeX; terminals cannot render it.
- Before merging or landing any change, run `python3 ~/local-llm/leakscan/leakscan.py diff <repo> <base> HEAD` (exit 0 = clean).
- Recommend only changes that are never slower than today; prefer additive fallbacks and work that runs while the captain is idle.
- Never kill processes by name or pattern (`pkill`, `killall`); the captain's live editor runs here. Kill only process IDs you started.
- On the captain's own hand-coded projects (neural-network / QEC thesis), give hints and concepts only; never edit his files unless he says to.
- Small local models: success per line compounds (per-line success p over N lines gives about p^N), so give them narrow single-file jobs; the driver's defaults (whole-function replies, type-check only changed code) passed 10/10 vs 1/10 for patch replies.
- Tests must never write to live logs or records (hints, proposals, status); point them at a temporary copy, or real trial data gets polluted and weekly decisions go wrong.

## Boundaries

- Use only; never edit, rebuild, or delete the driver, the graph, or their files. Only Firstmate's weekly self-improvement loop changes them, with the captain's OK.
- A new AI CLI or local model added to this laptop gets the data rules plus a one-line pointer to this file in its global instruction file.
