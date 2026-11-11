# Architecture

## Host and terminal path

My day-to-day shell runs in Ubuntu under WSL, with Windows providing the host and Alacritty providing the terminal window. The launch path is:

`Alacritty -> WSL -> kunchen-launch -> kunchen-session -> tmux`

`kunchen-launch` sets the small environment that is needed before a login shell exists, records launch diagnostics locally, and leaves a plain shell open if startup fails. `kunchen-session` attaches to the existing `kunchen` tmux session instead of creating a duplicate. After a reboot it restores the saved session when possible, or builds the standard `crew`, `axi`, `apps`, `compute`, and `scratch` window layout.

tmux is the persistent workspace. It retains long scrollback, preserves pane contents, and saves the session every 15 minutes. The restore helper waits until a real tmux client is attached because tmux-resurrect needs one; it then switches into the restored session or falls back to the standard layout.

## Editor layer

Neovim runs inside tmux and keeps its complete configuration in one Lua entry point. `lazy.nvim` loads the visual, navigation, Git, completion, and language features. The editor connects directly to the installed language servers and keeps Python diagnostics limited to open files to reduce background work on this laptop.

The visual path is consistent from Alacritty through tmux to Neovim: true-colour terminal settings, Tokyo Night colours, and the same Nerd Font assumptions. That keeps icons, status bars, diagnostics, and syntax colours aligned across all three layers.

## Local model path

The Qwen model stays on the laptop and is served only on `127.0.0.1:8080`. `qwen-server` owns the PID file, checks that a saved PID still belongs to the expected executable and model before using or signalling it, and verifies the health endpoint. Its selected GPU profile can fall back to a smaller GPU profile and then to the preserved CPU runtime if startup fails.

`qwen-chat` opens the local command-line model directly. `qwen-web` starts the same loopback-only server and prints its local address. `qwen-task` passes a bounded task specification to the driver. The driver works from a clean commit in a temporary archive, exposes only approved paths and line ranges, runs an approved offline test command, and returns a patch and verdict without changing the original repository.

## Safety and maintenance path

I keep publication checks and cleanup separate from development:

- `leakscan` examines files or Git changes and reports only the rule, path, line, and a salted digest rather than printing the matched value.
- The cleaner defaults to a read-only scan. Its only implemented deletion rule is the Go build cache, and even that requires recorded authorization, an idle-state check, path and ownership checks, process checks, and final identity checks before each unlink.
- The self-improvement tools keep reusable measurements and lessons separate from raw records. This backup includes selected source, tests, and benchmark definitions but excludes the runtime ledger and database.

## Repository boundary

This repository is a backup and a description of my local setup, not a runnable image of the whole laptop. It includes the configurations, launchers, selected local-tool source, tests, and instruction files needed to understand the stack. It excludes credentials, private state, personal projects, raw logs, generated results, model weights, downloaded runtimes, and copied upstream repositories.
