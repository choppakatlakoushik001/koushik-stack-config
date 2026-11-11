# Stack overview

I use Windows as the host and Ubuntu through WSL as the working environment. WSL starts with `systemd` enabled. Alacritty provides the terminal, tmux keeps long-running sessions together, and Neovim is the main editor.

The terminal and editor share one visual setup: true colour, a Tokyo Night palette, and JetBrainsMono Nerd Font. Alacritty opens maximized and keeps 10,000 lines of scrollback. tmux adds mouse support, a 100,000-line history, numbered windows and panes, activity indicators, and session restoration. Its continuum setting saves every 15 minutes, while the Neovim restore strategy reopens the editor session.

Neovim is configured in Lua with `lazy.nvim`. The setup includes Tree-sitter highlighting, Git signs, fuzzy search, completion and snippets, a file tree, buffer tabs, a status line, and language-server support. Python uses BasedPyright and Ruff; the same configuration also covers Go, JavaScript and TypeScript, shell, JSON, YAML, CSS, HTML, and Lua.

Firstmate sits on top of the tmux workspace as the supervision layer. I keep its upstream source separate and do not copy its private runtime data into this repository. The shared instruction files keep the same local-data boundaries and six-list task view across the command-line tools I use.

The local tooling fills in the rest of the workflow:

- The Qwen launchers run the local model server, chat, task driver, and web interface.
- The task driver gives the local model a small, bounded coding job in an isolated copy and never applies its patch to the source repository automatically.
- `leakscan` checks changes for credentials and personal data before publication.
- The cleaner reports disk and memory candidates conservatively; only its narrowly protected Go build-cache rule can delete files after an explicit command and live-use checks.
- The self-improvement source records measurements and reusable lessons while keeping raw records and runtime databases outside this backup.

The files under `backup/` are the local configurations and selected source snapshots. The files under `showcase/` explain how I use them. Credentials, personal projects, private runtime state, raw logs, databases, models, downloaded toolchains, and copied upstream source are deliberately left out.
