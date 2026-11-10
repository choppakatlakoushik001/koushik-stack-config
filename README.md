# Koushik Stack configuration backup

This snapshot contains the reviewed, non-secret configuration needed to reconstruct the terminal and shared CLI setup.
The intended destination is the private repository `choppakatlakoushik001/koushik-stack-config`, but no repository or remote has been created yet.

## Snapshot state

- This is a pre-rename snapshot.
- The current launcher files still use their existing `kunchen-*` names and `kunchen` session identifiers.
- The separate `kunchen` to `koushik` migration is intentionally deferred until a quiet window with no live workers.
- The upstream account name `kunchenguid` is an external identity and must never be renamed.
- No scheduled publishing or unattended push is included.

## Restore map

Paths under `home/` are relative to the Linux home directory:

| Snapshot path | Restore destination |
|---|---|
| `home/.tmux.conf` | `~/.tmux.conf` |
| `home/.config/nvim/init.lua` | `~/.config/nvim/init.lua` |
| `home/.local/bin/kunchen-launch` | `~/.local/bin/kunchen-launch` |
| `home/.local/bin/kunchen-session` | `~/.local/bin/kunchen-session` |
| `home/.local/bin/kunchen-restore` | `~/.local/bin/kunchen-restore` |
| `home/.claude/CLAUDE.md` | `~/.claude/CLAUDE.md` |
| `home/.codex/AGENTS.md` | `~/.codex/AGENTS.md` |
| `home/.gemini/GEMINI.md` | `~/.gemini/GEMINI.md` |
| `home/.qwen/QWEN.md` | `~/.qwen/QWEN.md` |
| `home/local-llm/SHARED-TOOLS.md` | `~/local-llm/SHARED-TOOLS.md` |
| `windows/AppData/Roaming/alacritty/alacritty.toml` | `%APPDATA%\\alacritty\\alacritty.toml` |

The Windows Start Menu shortcut and the full shell startup file are excluded.
The shortcut currently launches the dedicated launcher script, and the shell alias is `k` pointing to the session script; those small integration steps should be recreated deliberately instead of copying unrelated machine state.

## Deliberate exclusions

This repository must never contain authentication material, environment files, tokens, passwords, personal records, Job Pilot data, Firstmate `data/`, `state/`, `config/`, or `projects/`, model transcripts or memories, logs, generated outputs, installed plugins, Neovim undo/shada/swap data, tmux resurrection data, or caches.

Neovim backup revisions and `lazy-lock.json` are also excluded from this snapshot.
Only the reviewed source configuration is retained.

## Verification

`MANIFEST.sha256` records a SHA-256 checksum for every snapshot file except the manifest itself.
SHA-256 is a content fingerprint: if one byte changes, the recorded value changes.

Before any publication, run the local leak scanner against the exact snapshot, inspect the full diff and file list, and require the configured approval gate.
