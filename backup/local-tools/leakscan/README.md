# leakscan

Local-only scanner that catches secrets and personal data in worker changes and outbound commands before they land or leave. See CHARTER.md.

## Usage

```bash
# Scan added lines in git diff
python3 leakscan.py diff <git-dir> <base> <head>

# Scan files or directories
python3 leakscan.py paths <path>...

# Classify an outbound command
python3 leakscan.py command -- <argv...>
```

### Exit Codes
- `0`: Clean (no unapproved findings)
- `1`: Findings detected
- `2`: Usage or configuration error

### Allowlist
Add exceptions to `.leakscan-allow` in the format:
```
<rule> <path-glob> <reason>
```
Reason is strictly required.

### Findings Log
Findings are logged in append-only JSON lines to `~/local-llm/leakscan/findings.jsonl`.
Logged records never store or print the matched secret itself, only rule, path, line, and salted hash digest.

