# Leak scanner charter

## Captain's words (verbatim, dated)
- 2026-10-28: "tell every CLI to keep an eye on each of the other CLIs, to not leak any significant information, everything should stay inside my own secured environment"
- 2026-10-28: "Everything should be safe and secure (check once more), no data should be leaked at any cost (not just this any part from our whole setup)"
- 2026-10-28: proceed today when idle; master Claude, labour agy and Grok, split by token limit and intelligence.

## Requirements and status
| # | Requirement | Status | Where |
|---|---|---|---|
| L1 | Detect secrets (keys, tokens, private keys, passwords, .env content, high-entropy strings) in a git diff or paths | complete | leakscan.py |
| L2 | Detect personal data patterns (emails, phone numbers, the captain's name/addresses supplied via a local private list never committed) | complete | leakscan.py |
| L3 | Flag outbound commands (curl/wget POST, scp, rsync to remote, git push, gist, paste services, cloud CLIs) | complete | leakscan.py |
| L4 | Findings never contain the secret itself: only rule, file, line, and a salted hash | complete | leakscan.py |
| L5 | Exit non-zero on findings so a merge can be blocked; allowlist with reasons | complete | leakscan.py |
| L6 | Append-only findings log feeds the weekly self-improvement report | complete | leakscan.py |
| L7 | Stdlib only, no installs, no network | complete | leakscan.py |

## Hard rules
- Tests use only synthetic fake secrets. Never read real secrets, personal data or ~/.local/share/job-pilot.
- The private personal-pattern list lives outside the repo and is never committed or printed.
