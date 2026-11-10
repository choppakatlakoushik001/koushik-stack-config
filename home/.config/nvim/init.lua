-- ~/.config/nvim/init.lua
-- Lean Neovim for the hybrid terminal-cloud setup.
--
-- Four plugins, deliberately. Completion and LSP are Neovim 0.11 natives, so
-- there is no nvim-cmp / mason / lspconfig dependency chain to maintain.
-- Language servers are ordinary binaries on PATH (pyright, ruff), installed
-- alongside everything else in ~/.local and ~/.npm-global.

vim.g.mapleader = " "
vim.g.maplocalleader = " "

-- ---------------------------------------------------------------- options --
local o = vim.opt
o.number = true
o.relativenumber = false
o.signcolumn = "yes"           -- stop the gutter from jittering on diagnostics
o.expandtab = true
o.shiftwidth = 4
o.tabstop = 4
o.smartindent = true
o.wrap = false
o.ignorecase = true
o.smartcase = true
o.termguicolors = true
o.scrolloff = 8
o.splitright = true
o.splitbelow = true
o.undofile = true              -- undo survives closing the file
-- Swap files are ON deliberately. They are the only recovery net for a buffer
-- that was never written: if nvim or the pane dies, `nvim <file>` offers to
-- restore. With swapfile=false, unsaved work is simply gone -- which is exactly
-- how ~90 lines of test.py were lost on 2026-08-13.
o.swapfile = true
o.updatetime = 250
o.clipboard = "unnamedplus"    -- shares the Windows clipboard via win32yank.exe (~/.local/bin, on PATH)
o.completeopt = "menuone,noselect,popup"
o.mouse = "a"

-- --------------------------------------------------------------- bootstrap --
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.uv.fs_stat(lazypath) then
  vim.fn.system({
    "git", "clone", "--filter=blob:none", "--branch=stable",
    "https://github.com/folke/lazy.nvim.git", lazypath,
  })
end
vim.opt.rtp:prepend(lazypath)

require("lazy").setup({
  -- Matches your tmux status bar and Alacritty colours.
  {
    "folke/tokyonight.nvim",
    priority = 1000,
    config = function()
      require("tokyonight").setup({ style = "night" })
      vim.cmd.colorscheme("tokyonight")
    end,
  },

  -- Syntax that actually understands the code.
  {
    "nvim-treesitter/nvim-treesitter",
    branch = "master",           -- pinned: the `main` rewrite changes this API
    build = ":TSUpdate",
    config = function()
      require("nvim-treesitter.configs").setup({
        ensure_installed = {
          "python", "lua", "bash", "markdown", "markdown_inline",
          "json", "yaml", "toml", "go", "rust", "javascript", "typescript",
        },
        highlight = { enable = true },
        indent = { enable = true },
      })
    end,
  },

  -- Fuzzy everything. Uses the fzf and ripgrep binaries, not a Lua reimpl.
  {
    "ibhagwan/fzf-lua",
    config = function()
      require("fzf-lua").setup({ "telescope", winopts = { height = 0.85, width = 0.85 } })
    end,
  },

  -- Git state in the gutter.
  {
    "lewis6991/gitsigns.nvim",
    config = function() require("gitsigns").setup() end,
  },

  -- Completion. Pinned to a release tag so lazy.nvim pulls the PREBUILT Rust
  -- fuzzy matcher instead of trying to compile it — no cargo needed.
  {
    "saghen/blink.cmp",
    version = "1.*",
    dependencies = { "rafamadriz/friendly-snippets" },
    opts = {
      keymap = {
        preset = "super-tab",              -- Tab accepts, like VS Code
        ["<CR>"] = { "accept", "fallback" }, -- and so does Enter
      },
      appearance = {
        nerd_font_variant = "mono",        -- matches JetBrainsMono NFM
      },
      completion = {
        -- Pop the menu on every keystroke, not just after . [ " '
        -- This is the single biggest difference from the native setup.
        trigger = { show_on_keyword = true },
        list = { selection = { preselect = true, auto_insert = false } },
        documentation = { auto_show = true, auto_show_delay_ms = 200 },
        menu = { draw = { treesitter = { "lsp" } } },
        ghost_text = { enabled = false },  -- reserved for AI suggestions later
      },
      sources = {
        default = { "lsp", "path", "snippets", "buffer" },
      },
      signature = { enabled = true },      -- parameter hints while calling
      fuzzy = { implementation = "prefer_rust_with_warning" },
    },
    opts_extend = { "sources.default" },
  },

  -- ------------------------------------------------- the VS Code furniture --
  -- Everything below is presentation, not capability. The LSP and completion
  -- above are what actually make this an IDE; these three make it LOOK like
  -- one, which matters when you are still learning where things live.

  -- Persistent file tree down the left, like the VS Code Explorer. netrw
  -- (<leader>e used to open it) is a full-window file listing that replaces
  -- your buffer; neo-tree is a real sidebar that stays put beside the code.
  {
    "nvim-neo-tree/neo-tree.nvim",
    branch = "v3.x",
    dependencies = {
      "nvim-lua/plenary.nvim",
      "MunifTanjim/nui.nvim",
      "nvim-tree/nvim-web-devicons",
    },
    opts = {
      close_if_last_window = true,   -- don't leave a lone sidebar holding nvim open
      window = { width = 32 },
      filesystem = {
        follow_current_file = { enabled = true },  -- tree tracks the open buffer
        use_libuv_file_watcher = true,             -- reflect external file changes
        filtered_items = {
          hide_dotfiles = false,     -- you edit .config and .github constantly
          hide_gitignored = true,
          hide_by_name = { ".git", "node_modules", "__pycache__", ".venv" },
        },
      },
    },
  },

  -- Buffer tabs across the top. Neovim's own :tabs are workspaces, not file
  -- tabs, which is why open files feel invisible without this.
  {
    "akinsho/bufferline.nvim",
    version = "*",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    opts = {
      options = {
        diagnostics = "nvim_lsp",    -- error badge on the tab, like VS Code
        show_buffer_close_icons = false,
        offsets = {
          -- Keeps the tab bar from sliding under the tree.
          { filetype = "neo-tree", text = "EXPLORER", highlight = "Directory" },
        },
      },
    },
  },

  -- Status line: mode, branch, diagnostics, position.
  {
    "nvim-lualine/lualine.nvim",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    opts = { options = { theme = "tokyonight", globalstatus = true } },
  },

  -- Indent guides: a plain light dotted vertical line at every indent
  -- level, running the full height of the file uniformly (no per-scope
  -- highlighting) - just a constant visual marker of how deep the next
  -- line you type will land, since Python has no braces to show that.
  {
    "lukas-reineke/indent-blankline.nvim",
    main = "ibl",
    opts = {
      indent = { char = "┊", highlight = "IblIndent" },
      scope = { enabled = false },
    },
    config = function(_, opts)
      require("ibl").setup(opts)
      -- Explicit color instead of trusting tokyonight's own ibl styling,
      -- so this stays correct even if the theme's plugin support changes.
      vim.api.nvim_set_hl(0, "IblIndent", { fg = "#3b4261" })  -- dim, matches Alacritty's bright black
    end,
  },
}, {
  ui = { border = "rounded" },
  change_detection = { notify = false },
})

-- --------------------------------------------------------------------- LSP --
-- Configured with the native 0.11 API. No lspconfig plugin needed.

-- Point pyright at the project's own .venv when there is one. This is what
-- makes ~/lab (and any uv project) resolve imports correctly with zero setup.
-- Deliberately NO on_init venv hook.
--
-- An earlier version of this file mutated client settings at runtime to point
-- the server at <root>/.venv/bin/python. That hook was writing to the wrong
-- table AND was unnecessary: basedpyright/pyright already auto-detect a
-- ./.venv in the project root. Removing it keeps `settings` below static,
-- which is the only form that reliably reaches the server on Neovim 0.11.

local py_roots = { "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", ".venv", ".git" }

-- Advertise blink.cmp's capabilities to every server, so completion items come
-- back with snippets, docs and resolve support rather than bare labels.
local ok_blink, blink = pcall(require, "blink.cmp")
local capabilities = ok_blink and blink.get_lsp_capabilities() or nil

-- basedpyright: community fork of pyright that restores the features Microsoft
-- keeps exclusive to Pylance — inlay hints, semantic tokens, and auto-import
-- completions on by default. Same type-inference engine underneath.
vim.lsp.config("basedpyright", {
  cmd = { "basedpyright-langserver", "--stdio" },
  filetypes = { "python" },
  root_markers = py_roots,
  capabilities = capabilities,
  settings = {
    basedpyright = {
      analysis = {
        -- basedpyright defaults to "recommended", which is stricter than
        -- anything you have written and will bury real errors under noise.
        -- "standard" matches what VS Code/Pylance actually gives you.
        typeCheckingMode = "standard",
        autoImportCompletions = true,
        autoSearchPaths = true,
        useLibraryCodeForTypes = true,
        diagnosticMode = "openFilesOnly",   -- keep it light on a 16GB laptop
        inlayHints = {
          variableTypes = true,
          callArgumentNames = true,
          functionReturnTypes = true,
          genericTypes = false,
        },
      },
    },
  },
})

vim.lsp.config("ruff", {
  cmd = { "ruff", "server" },
  filetypes = { "python" },
  root_markers = py_roots,
  capabilities = capabilities,
})

-- --- other languages ---------------------------------------------------------
-- Same pattern throughout: a binary on PATH, root markers, blink capabilities.
local servers = {
  gopls = {
    cmd = { "gopls" },
    filetypes = { "go", "gomod", "gowork", "gotmpl" },
    root_markers = { "go.work", "go.mod", ".git" },
    settings = { gopls = { analyses = { unusedparams = true }, staticcheck = true } },
  },
  ts_ls = {
    cmd = { "typescript-language-server", "--stdio" },
    filetypes = { "javascript", "javascriptreact", "typescript", "typescriptreact" },
    root_markers = { "tsconfig.json", "jsconfig.json", "package.json", ".git" },
    -- Without this the server exits at init with "Could not find a valid
    -- TypeScript installation": it only looks in the workspace's node_modules,
    -- so a globally-installed typescript is invisible to it. Projects that
    -- vendor their own typescript override this automatically.
    init_options = {
      tsserver = {
        path = vim.fn.expand("~/.npm-global/lib/node_modules/typescript/lib"),
      },
    },
  },
  bashls = {
    cmd = { "bash-language-server", "start" },
    filetypes = { "sh", "bash" },
    root_markers = { ".git" },
  },
  jsonls = {
    cmd = { "vscode-json-language-server", "--stdio" },
    filetypes = { "json", "jsonc" },
    root_markers = { ".git" },
    init_options = { provideFormatter = true },
  },
  yamlls = {
    cmd = { "yaml-language-server", "--stdio" },
    filetypes = { "yaml" },
    root_markers = { ".git" },
    settings = { yaml = { keyOrdering = false } },
  },
  cssls = {
    cmd = { "vscode-css-language-server", "--stdio" },
    filetypes = { "css", "scss", "less" },
    root_markers = { "package.json", ".git" },
  },
  html = {
    cmd = { "vscode-html-language-server", "--stdio" },
    filetypes = { "html" },
    root_markers = { "package.json", ".git" },
  },
  lua_ls = {
    cmd = { "lua-language-server" },
    filetypes = { "lua" },
    root_markers = { ".luarc.json", ".stylua.toml", ".git" },
    settings = {
      Lua = {
        runtime = { version = "LuaJIT" },
        -- Stops it flagging `vim` as undefined in your own nvim config.
        diagnostics = { globals = { "vim" } },
        workspace = { library = vim.api.nvim_get_runtime_file("", true), checkThirdParty = false },
        telemetry = { enable = false },
      },
    },
  },
}

local enabled = { "basedpyright", "ruff" }
for name, cfg in pairs(servers) do
  -- Only enable a server whose binary actually exists, so a missing tool is a
  -- silent no-op instead of an error popup every time you open a file.
  if vim.fn.executable(cfg.cmd[1]) == 1 then
    cfg.capabilities = capabilities
    vim.lsp.config(name, cfg)
    table.insert(enabled, name)
  end
end

vim.lsp.enable(enabled)

vim.diagnostic.config({
  virtual_text = { prefix = "●" },
  severity_sort = true,
  float = { border = "rounded" },
})

vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(ev)
    local client = vim.lsp.get_client_by_id(ev.data.client_id)
    local map = function(keys, fn, desc)
      vim.keymap.set("n", keys, fn, { buffer = ev.buf, desc = desc })
    end

    map("gd", vim.lsp.buf.definition, "goto definition")
    map("gr", vim.lsp.buf.references, "references")
    map("gi", vim.lsp.buf.implementation, "implementation")
    map("K", vim.lsp.buf.hover, "hover docs")
    map("<leader>rn", vim.lsp.buf.rename, "rename symbol")
    map("<leader>ca", vim.lsp.buf.code_action, "code action")
    map("<leader>fm", function() vim.lsp.buf.format({ async = true }) end, "format")
    map("[d", function() vim.diagnostic.jump({ count = -1 }) end, "prev diagnostic")
    map("]d", function() vim.diagnostic.jump({ count = 1 }) end, "next diagnostic")

    -- NOTE: native completion (vim.lsp.completion.enable) is deliberately NOT
    -- enabled here — blink.cmp owns the completion menu. Running both puts two
    -- popups on the same keystroke. If you ever remove blink, re-enable it:
    --   vim.lsp.completion.enable(true, client.id, ev.buf, { autotrigger = true })

    -- Inferred types shown inline, the way Pylance does it.
    if client and client:supports_method("textDocument/inlayHint") then
      vim.lsp.inlay_hint.enable(true, { bufnr = ev.buf })
      map("<leader>ih", function()
        vim.lsp.inlay_hint.enable(
          not vim.lsp.inlay_hint.is_enabled({ bufnr = ev.buf }), { bufnr = ev.buf })
      end, "toggle inlay hints")
    end
  end,
})

-- ---------------------------------------------------------------- keymaps --
local map = vim.keymap.set
local fzf = function(cmd) return function() require("fzf-lua")[cmd]() end end

map("n", "<leader>f", fzf("files"),      { desc = "find files" })
map("n", "<leader>g", fzf("live_grep"),  { desc = "grep project" })
map("n", "<leader>b", fzf("buffers"),    { desc = "buffers" })
map("n", "<leader>d", fzf("diagnostics_document"), { desc = "diagnostics" })
map("n", "<leader>h", fzf("helptags"),   { desc = "help" })
map("n", "<leader>e", "<cmd>Neotree toggle<cr>",           { desc = "toggle file tree" })
map("n", "<leader>E", "<cmd>Neotree reveal<cr>",           { desc = "reveal current file in tree" })
map("n", "<leader>w", vim.cmd.write,     { desc = "write" })

-- VS Code muscle memory. Ctrl+P is the one everyone reaches for first.
--
-- NOTE: VS Code's Ctrl+B (toggle sidebar) is deliberately NOT mapped — Ctrl+B
-- is the tmux prefix and never reaches Neovim. <leader>e is the sidebar toggle.
map("n", "<C-p>", fzf("files"),      { desc = "quick open (VS Code Ctrl+P)" })

-- Buffer tabs: Shift+H / Shift+L step through the bufferline, the way Ctrl+Tab
-- cycles editors in VS Code. This costs you default H/L (jump to top/bottom of
-- the screen), which gg/G and Ctrl+D/Ctrl+U already cover better.
map("n", "H", "<cmd>BufferLineCyclePrev<cr>", { desc = "previous buffer tab" })
map("n", "L", "<cmd>BufferLineCycleNext<cr>", { desc = "next buffer tab" })
map("n", "<leader>x", "<cmd>bdelete<cr>",     { desc = "close buffer tab" })

-- Tab/Shift+Tab indent, the way VS Code's editor uses them: with lines
-- selected they shift the whole block right/left and keep the selection
-- (gv) so you can repeat the press; with nothing selected they indent/
-- outdent the current line. Insert-mode Tab is untouched — expandtab +
-- shiftwidth=4 above already makes it insert a 4-space tab there.
map("n", "<Tab>",   ">>",  { desc = "indent line" })
map("n", "<S-Tab>", "<<",  { desc = "outdent line" })
map("v", "<Tab>",   ">gv", { desc = "indent selection" })
map("v", "<S-Tab>", "<gv", { desc = "outdent selection" })

-- Ctrl+/ toggles comments the way VS Code does: on the current line in normal
-- mode, on the whole highlighted block in visual mode, and pressing it again
-- uncomments. Neovim 0.11 already ships the gcc/gc commenting operators and
-- picks the right comment syntax per filetype (# for Python, // for Lua/JS,
-- <!-- --> for HTML), so these only put them on the VS Code key.
-- Both spellings are mapped on purpose: most terminals send Ctrl+/ as the
-- control character <C-_>, while terminals speaking the kitty keyboard
-- protocol send a literal <C-/>. remap = true is required because gcc/gc are
-- themselves default mappings, not raw keys.
for _, key in ipairs({ "<C-/>", "<C-_>" }) do
  map("n", key, "gcc",      { remap = true, desc = "toggle comment (line)" })
  map("x", key, "gc",       { remap = true, desc = "toggle comment (selection)" })
  map("i", key, "<C-o>gcc", { remap = true, desc = "toggle comment (line)" })
end

-- Plain / as the comment toggle for a highlighted block, because Ctrl+/ does
-- not survive the trip through this terminal. This only rebinds / in VISUAL
-- mode, where it is the rarely-used "extend the selection to a search match";
-- normal-mode / is still search and is deliberately left alone.
map("x", "/", "gc", { remap = true, desc = "toggle comment (selection)" })

-- Ctrl+C / Ctrl+V as copy/paste, the way every other app behaves — vim's own
-- y/p work great once you know them, but these make the muscle-memory keys
-- do the same thing while that's still sinking in.
--
-- Nothing is lost by remapping these: Visual mode's default <C-c> just backs
-- out like Esc with no side effect, so yanking there instead costs nothing.
-- Normal mode's default <C-v> starts blockwise Visual — moved to <leader>vb
-- so it is still one keystroke away. Insert mode's default <C-v> inserts the
-- next character literally (e.g. a real tab); <C-q> is vim's own documented
-- synonym for that (:help i_CTRL-Q), kept as the escape hatch.
map("v", "<C-c>", '"+y',    { desc = "copy selection (like Ctrl+C elsewhere)" })
map("n", "<C-v>", '"+p',    { desc = "paste after cursor (like Ctrl+V elsewhere)" })
map("i", "<C-v>", "<C-r>+", { desc = "paste (like Ctrl+V elsewhere)" })
map("n", "<leader>vb", "<C-v>", { desc = "blockwise visual (Ctrl+V's original job)" })

-- ------------------------------------------------------- build & run ------
-- Compile (where the language needs it) and execute the current file.
--
--   <leader>r   output in a split INSIDE nvim  — never leave the editor
--   <leader>R   output in the tmux pane next door
--
-- Compiled languages get "compile && run" as one command, so a compile error
-- stops before execution and you see the compiler message, not a stale binary.

-- A terminal cannot draw an image, so anything visual has to land in a real
-- browser instead. VIZ_DIR is the shared drop point for the files behind it.
-- with_visual_watch() (below) wraps every build_cmd() result at the shell
-- level, so it is language-agnostic: any filetype in the table further down
-- gets its output shown, not just Python. Python needs no code from you (see
-- the environment below); another language opts in by writing an image, SVG or
-- HTML into $VIZSHOW_DIR (or anywhere in the project, for images), or by
-- calling `vizopen <path>` itself.
local VIZ_DIR = "/mnt/c/Users/chopp/AppData/Local/Temp/pyviz"

-- `vizopen` now feeds the "viz hub": ONE browser tab that collects every visual
-- as it is produced, the way a Jupyter notebook collects cell outputs. A tab is
-- opened only when none is visible, so a second run updates the tab you already
-- have, and re-running a script REPLACES its old cards instead of stacking new
-- ones. What makes Python need no code at all is this environment, which every
-- program launched from here inherits:
--   MPLBACKEND  plt.show() (and unshown figures, at exit) go to the hub
--   PYTHONPATH  a start-up hook that points Plotly's fig.show() at the hub
--   PATH        an xdg-open shim, so PIL's Image.show() and similar reach it
--   BROWSER     Python's webbrowser module and most CLIs reach it
local VIZ_HUB  = vim.fn.expand("~/.local/lib/vizhub")
local VIZ_HOOK = vim.fn.expand("~/.local/lib/vizhook")
local VIZ_PY   = vim.fn.expand("~/.local/lib/pyviz")
local VIZ_OPEN = vim.fn.expand("~/.local/bin/vizopen")
local VIZ_RUN  = vim.fn.expand("~/.local/bin/vizrun")

-- Shell form of that environment, from `vizrun --env` so it lives in one place.
-- tmux panes do not inherit Neovim's own environment, so a bare shell opened
-- from here has to load it explicitly.
local function viz_exports()
  return ("eval \"$(%s --env)\"; "):format(vim.fn.shellescape(VIZ_RUN))
end

-- The same environment for Neovim itself, so :terminal and :! get it too.
-- (Keep in step with `vizrun --env`.)
vim.env.NVIM_VIZ    = "1"
vim.env.MPLBACKEND  = "module://nvimviz_mpl"
vim.env.BROWSER     = VIZ_OPEN
vim.env.VIZSHOW_DIR = VIZ_DIR
vim.env.PATH        = VIZ_HUB .. "/bin:" .. vim.env.PATH
vim.env.PYTHONPATH  = VIZ_HOOK .. ":" .. VIZ_PY .. (vim.env.PYTHONPATH and (":" .. vim.env.PYTHONPATH) or "")

-- Beyond what a program asks to show, `vizrun` also catches anything it WRITES:
-- after the run, any image or SVG newer than the start (savefig("out.png"), a
-- rendered diagram) inside the project or the drop folder goes to the hub too.
-- It also sets the environment above, so the line typed into a tmux pane is one
-- short command instead of a page of shell.
local function with_visual_watch(cmd, root)
  return ("%s %s %s"):format(vim.fn.shellescape(VIZ_RUN),
    vim.fn.shellescape(root or vim.fn.getcwd()), vim.fn.shellescape(cmd))
end

local function build_cmd()
  local file = vim.fn.shellescape(vim.fn.expand("%:p"))
  local stem = vim.fn.expand("%:t:r")
  local bin  = vim.fn.shellescape("/tmp/nvim-run-" .. stem)
  local root = vim.fs.root(0, { "pyproject.toml", "go.mod", "package.json", "Cargo.toml", ".git" })
    or vim.fn.getcwd()

  -- Prefer the project's own interpreter so imports resolve the same way the
  -- language server sees them.
  local py = root .. "/.venv/bin/python"
  if vim.fn.executable(py) == 0 then py = "python3" end

  local cmd = ({
    python     = py .. " " .. file,
    javascript = "node " .. file,
    typescript = "npx tsx " .. file,
    lua        = "lua " .. file,
    sh         = "bash " .. file,
    bash       = "bash " .. file,
    go         = "go run " .. file,                    -- compiles + runs
    rust       = "cargo run",
    c          = "gcc -Wall -O2 " .. file .. " -o " .. bin .. " && " .. bin,
    cpp        = "g++ -Wall -O2 -std=c++17 " .. file .. " -o " .. bin .. " && " .. bin,
    java       = "javac -d /tmp " .. file .. " && java -cp /tmp " .. stem,
  })[vim.bo.filetype]

  return cmd and with_visual_watch(cmd, root) or nil
end

-- Keep one runner window instead of stacking a new terminal on every run.
local runner_buf = nil

local function run_in_nvim()
  vim.cmd("write")
  local cmd = build_cmd()
  if not cmd then
    vim.notify("no run command for filetype: " .. vim.bo.filetype, vim.log.levels.WARN)
    return
  end

  if runner_buf and vim.api.nvim_buf_is_valid(runner_buf) then
    vim.api.nvim_buf_delete(runner_buf, { force = true })
  end

  local editor = vim.api.nvim_get_current_win()
  vim.cmd("botright 15split")
  vim.cmd("terminal " .. cmd)
  runner_buf = vim.api.nvim_get_current_buf()

  vim.bo[runner_buf].buflisted = false
  -- q closes the output; the editor window is never disturbed.
  vim.keymap.set("n", "q", "<cmd>close<cr>", { buffer = runner_buf, nowait = true })
  vim.cmd("normal! G")               -- follow the output as it arrives
  vim.api.nvim_set_current_win(editor)  -- typing continues in your code
end

local function run_in_tmux()
  vim.cmd("write")
  local cmd = build_cmd()
  if not cmd then
    vim.notify("no run command for filetype: " .. vim.bo.filetype, vim.log.levels.WARN)
    return
  end
  if vim.env.TMUX then
    local panes = tonumber(vim.fn.system({ "tmux", "display-message", "-p", "#{window_panes}" }))
    if panes and panes > 1 then
      -- `.+` is the next pane; without the count check it wraps around and
      -- types the command into this very pane.
      vim.fn.system({ "tmux", "send-keys", "-t", ".+", cmd, "Enter" })
      return
    end
  end
  run_in_nvim()
end

map("n", "<leader>r", run_in_nvim, { desc = "build+run (output inside nvim)" })
map("n", "<leader>R", run_in_tmux, { desc = "build+run (output in tmux pane)" })

-- ------------------------------------------------ project tmux terminals --
-- <leader>R reuses the *next* pane, which is what you want while iterating on
-- one file. These open a NEW pane instead, so several files can run side by
-- side, each with its own scrollback.
--
-- Every pane starts at the project root with the project's venv ahead of it on
-- PATH, so `python`, `pytest` and `qec` resolve to the project interpreter
-- without activating anything by hand.

local function project_root()
  return vim.fs.root(0, { "pyproject.toml", "go.mod", "package.json", "Cargo.toml", ".git" })
    or vim.fn.getcwd()
end

-- keep_focus=true leaves the cursor in Neovim (tmux's -d flag). Runners want
-- that: you fire off a file and carry on typing. A bare shell does not -- you
-- opened it to type in it, so focus should follow.
local function tmux_pane(direction, shell_cmd, keep_focus)
  if not vim.env.TMUX then
    vim.notify("not inside tmux -- falling back to a split in nvim", vim.log.levels.WARN)
    run_in_nvim()
    return
  end

  local root = project_root()
  local prelude = viz_exports()     -- so plots typed by hand in this pane reach the browser too
  local venv = root .. "/.venv"
  if vim.fn.isdirectory(venv) == 1 then
    prelude = prelude .. ("export VIRTUAL_ENV=%s PATH=%s:$PATH; "):format(
      vim.fn.shellescape(venv), vim.fn.shellescape(venv .. "/bin"))
  end

  -- `exec bash` keeps the pane alive once the program exits, so the traceback
  -- stays on screen and Up-Arrow re-runs it. Without it tmux closes the pane
  -- the instant python returns.
  local inner = prelude .. (shell_cmd and (shell_cmd .. "; ") or "") .. "exec bash"

  -- -t $TMUX_PANE is not optional: without it tmux splits the session's ACTIVE
  -- window, so panes appear in whatever window you happen to be looking at
  -- rather than next to the file that launched them.
  local args = { "tmux", "split-window", direction, "-t", vim.env.TMUX_PANE }
  if keep_focus then table.insert(args, "-d") end
  vim.list_extend(args, { "-c", root, "bash", "-c", inner })
  vim.fn.system(args)
end

-- Bare shells at the project root. Focus follows into them.
map("n", "<leader>tv", function() tmux_pane("-h") end, { desc = "tmux shell: split right" })
map("n", "<leader>ts", function() tmux_pane("-v") end, { desc = "tmux shell: split below" })

-- Run THIS file in its own new pane, so each file gets a dedicated terminal.
local function run_in_new_pane(direction)
  return function()
    vim.cmd("write")
    local cmd = build_cmd()
    if not cmd then
      vim.notify("no run command for filetype: " .. vim.bo.filetype, vim.log.levels.WARN)
      return
    end
    tmux_pane(direction, cmd, true)   -- true: stay in the editor
  end
end

map("n", "<leader>tr", run_in_new_pane("-h"), { desc = "run this file in a new pane (right)" })
map("n", "<leader>tR", run_in_new_pane("-v"), { desc = "run this file in a new pane (below)" })

-- Whole-project commands, each in its own pane. Focus stays in the editor.
map("n", "<leader>tt", function() tmux_pane("-v", "pytest -v", true) end, { desc = "tmux: pytest" })
map("n", "<leader>tl", function() tmux_pane("-v", "ruff check .", true) end, { desc = "tmux: ruff" })

-- ------------------------------------------------------- visual previews --
-- Auto-opening is now handled centrally by with_visual_watch() (above,
-- wrapped into every build_cmd() result), so EVERY run keymap already opens
-- new previews for any language, not just via <leader>v. This section is
-- just the two manual extras on top of that: rerun-in-a-pane, and reopen
-- the newest render without rerunning anything.
--
-- <leader>v works in any project: vizshow sits on PYTHONPATH, not in a venv.
map("n", "<leader>v", run_in_new_pane("-v"), { desc = "run file + render its previews" })

-- Bring the hub tab back (closed it, or lost it behind other tabs). Every card
-- shown so far is still there.
map("n", "<leader>V", function()
  vim.system({ VIZ_OPEN, "--hub" }, { text = true }, function(r)
    vim.schedule(function()
      if r.code == 0 then vim.notify("viz hub opened")
      else vim.notify("viz hub failed: " .. vim.trim(r.stderr or ""), vim.log.levels.ERROR) end
    end)
  end)
end, { desc = "open the browser viz hub (all previews)" })

-- Insert the import line, so you never have to remember it.
map("n", "<leader>vi", function()
  vim.api.nvim_buf_set_lines(0, 0, 0, false, { "from vizshow import show" })
  vim.notify("added: from vizshow import show")
end, { desc = "insert vizshow import at top of file" })

-- F5 does the same from ANY mode, including insert. <leader>r is normal-mode
-- only, so while you are still building modal-editing muscle memory, pressing
-- space+r mid-typing just inserts the characters " r" into your file.
-- F5 always works: it leaves insert mode for you first.
map({ "n", "i", "v" }, "<F5>", function()
  vim.cmd("stopinsert")
  run_in_nvim()
end, { desc = "build+run from any mode" })

-- ------------------------------------------------ files that ARE visuals ---
-- Notebook-style: things that are pictures show up as pictures, no command.
--
--   image / pdf     opening one (from the tree, :e, the CLI) shows it in the
--                   browser instead of dumping binary into a buffer
--   svg, mermaid    render on save, the FIRST time; every later save refreshes
--   dot, ipynb      the same card in place. Notebooks also render on open.
--   markdown        same, but only when it contains something to look at (a
--                   mermaid/dot diagram or an image) -- plain prose stays text
--   html            never auto-opened (it is usually a web app you are
--                   building), but refreshes once you have previewed it
--   <leader>p       preview THIS file now, whatever it is
--
-- Refreshes use --update-only: they never open a new tab, so autosave (which
-- saves on leaving insert mode) cannot spray tabs at you while you type.

local function viz_push(path, opts)
  opts = opts or {}
  local cmd = { VIZ_OPEN }
  if opts.update then table.insert(cmd, "--update-only") end
  if opts.force  then table.insert(cmd, "--force") end
  table.insert(cmd, path)
  vim.system(cmd, { text = true }, function(r)
    if not opts.notify then return end
    vim.schedule(function()
      if r.code == 0 then vim.notify(vim.trim(r.stdout ~= "" and r.stdout or "shown in browser"))
      else vim.notify("vizopen: " .. vim.trim((r.stderr or "") .. (r.stdout or "")), vim.log.levels.ERROR) end
    end)
  end)
end

-- Both cases of each extension: autocmd patterns are case-sensitive.
local function ext_patterns(exts)
  local out = {}
  for _, e in ipairs(exts) do
    table.insert(out, "*." .. e)
    table.insert(out, "*." .. e:upper())
  end
  return out
end

local viz_group = vim.api.nvim_create_augroup("viz_hub", { clear = true })

-- Binary pictures: hand them to the browser, leave a note in the buffer.
vim.api.nvim_create_autocmd("BufReadCmd", {
  group = viz_group,
  pattern = ext_patterns({ "png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "pdf" }),
  callback = function(a)
    local path = vim.fn.fnamemodify(a.file, ":p")
    viz_push(path, { notify = true })
    vim.api.nvim_buf_set_lines(a.buf, 0, -1, false, {
      "", "  " .. vim.fn.fnamemodify(path, ":t") .. " is shown in the browser viz hub.", "",
      "  <leader>V   bring the hub tab back", "  <leader>p   send it again",
    })
    vim.bo[a.buf].buftype = "nofile"
    vim.bo[a.buf].modifiable = false
    vim.bo[a.buf].modified = false
  end,
})

-- Does this markdown have anything worth rendering?
local function markdown_has_visuals(buf)
  local text = table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
  return text:find("```%s*mermaid") or text:find("```%s*dot") or text:find("```%s*graphviz")
    or text:find("!%[[^%]]*%]%(") or false
end

local auto_first = { svg = true, mmd = true, mermaid = true, dot = true, gv = true, ipynb = true }

vim.api.nvim_create_autocmd("BufWritePost", {
  group = viz_group,
  pattern = ext_patterns({ "svg", "mmd", "mermaid", "dot", "gv", "ipynb", "md", "markdown", "html", "htm" }),
  callback = function(a)
    local ext = vim.fn.fnamemodify(a.file, ":e"):lower()
    local path = vim.fn.fnamemodify(a.file, ":p")
    local wants_first = auto_first[ext] or ((ext == "md" or ext == "markdown") and markdown_has_visuals(a.buf))
    if wants_first and not vim.b[a.buf].viz_started then
      vim.b[a.buf].viz_started = true
      viz_push(path)                       -- first time: may open the tab
    else
      viz_push(path, { update = true })    -- afterwards: refresh only
    end
  end,
})

-- A notebook is opened to be looked at, so render it on open.
vim.api.nvim_create_autocmd("BufReadPost", {
  group = viz_group,
  pattern = ext_patterns({ "ipynb" }),
  callback = function(a)
    vim.b[a.buf].viz_started = true
    viz_push(vim.fn.fnamemodify(a.file, ":p"))
  end,
})

local previewable = {}
for e in ("png jpg jpeg gif webp bmp ico avif svg pdf html htm md markdown mmd mermaid dot gv ipynb txt log csv json"):gmatch("%S+") do
  previewable[e] = true
end

map("n", "<leader>p", function()
  local path = vim.fn.expand("%:p")
  if path == "" then return end
  if not previewable[vim.fn.expand("%:e"):lower()] then
    -- Kept short on purpose: a message wider than the screen forces a hit-enter prompt.
    vim.notify("not a visual file -- <leader>r runs it", vim.log.levels.INFO)
    return
  end
  vim.cmd("silent! update")
  vim.b.viz_started = true
  viz_push(path, { force = true, notify = true })
end, { desc = "preview this file in the browser" })

-- ---------------------------------------------- Windows-style undo/redo --
-- Ctrl+Z undo, Ctrl+Y redo, from every editing mode.
--
-- Two defaults are given up for this, both deliberately:
--   Ctrl+Z normally SUSPENDS nvim into the background. Use :suspend if you
--     ever want that, or Ctrl+B d to detach the whole tmux session.
--   Ctrl+Y normally scrolls the view up one line. Ctrl+E still scrolls down;
--     Ctrl+U / Ctrl+D remain the sane way to move by half a screen.
--
-- blink.cmp's super-tab preset binds Ctrl+E, Tab and Shift-Tab but NOT Ctrl+Y,
-- so redo does not fight the completion menu.
--
-- One behavioural note: nvim groups an entire insert session into a single
-- undo step, so Ctrl+Z in insert mode removes everything typed since you last
-- pressed i -- not one character. That is vim's model, not a bug. Press Esc
-- periodically if you want finer-grained undo points.
map({ "n", "i" }, "<C-z>", "<cmd>undo<cr>", { desc = "undo (Windows style)" })
map({ "n", "i" }, "<C-y>", "<cmd>redo<cr>", { desc = "redo (Windows style)" })

-- Visual mode drops the selection first: undoing with a selection active
-- would otherwise operate on a range you can no longer see.
map("v", "<C-z>", "<Esc><cmd>undo<cr>", { desc = "undo (Windows style)" })
map("v", "<C-y>", "<Esc><cmd>redo<cr>", { desc = "redo (Windows style)" })

-- Ctrl+S saves from any mode too, for the same reason.
map({ "n", "i", "v" }, "<C-s>", function()
  vim.cmd("stopinsert")
  vim.cmd("write")
end, { desc = "save from any mode" })

-- Escape leaves terminal-insert mode, so you can scroll the output normally.
map("t", "<Esc><Esc>", [[<C-\><C-n>]], { desc = "leave terminal mode" })
map("n", "<Esc>",     vim.cmd.nohlsearch,{ desc = "clear search highlight" })

-- Ctrl+hjkl moves between splits. tmux owns Ctrl+B, so there is no conflict.
map("n", "<C-h>", "<C-w>h")
map("n", "<C-j>", "<C-w>j")
map("n", "<C-k>", "<C-w>k")
map("n", "<C-l>", "<C-w>l")

-- Visual-mode line moving.
map("v", "J", ":m '>+1<CR>gv=gv")
map("v", "K", ":m '<-2<CR>gv=gv")

-- Keep the cursor centred when jumping.
map("n", "<C-d>", "<C-d>zz")
map("n", "<C-u>", "<C-u>zz")

-- ------------------------------------------------------------------ misc --
-- Flash on yank, so you can see what you actually copied.
vim.api.nvim_create_autocmd("TextYankPost", {
  callback = function() vim.hl.on_yank({ timeout = 150 }) end,
})

-- Autosave: write a named, modified buffer whenever you leave it or the
-- terminal loses focus. Only touches buffers that already have a filename --
-- an unnamed scratch buffer has nowhere to go, so it is left alone and the
-- swapfile above is what protects it.
vim.api.nvim_create_autocmd({ "BufLeave", "FocusLost", "InsertLeave" }, {
  callback = function(ev)
    local bo = vim.bo[ev.buf]
    if bo.modified and bo.buftype == "" and bo.modifiable
       and vim.api.nvim_buf_get_name(ev.buf) ~= "" then
      vim.api.nvim_buf_call(ev.buf, function() vim.cmd("silent! write") end)
    end
  end,
})

-- Strip trailing whitespace on save for code files.
vim.api.nvim_create_autocmd("BufWritePre", {
  pattern = { "*.py", "*.lua", "*.sh", "*.go", "*.rs", "*.js", "*.ts", "*.md" },
  callback = function()
    local pos = vim.api.nvim_win_get_cursor(0)
    vim.cmd([[%s/\s\+$//e]])
    vim.api.nvim_win_set_cursor(0, pos)
  end,
})

-- --------------------------------------------------- neo-tree context menu --
-- Mouse-only file create/move/delete/rename, click-driven rather than
-- drag-driven: right-click a file/folder to pop a small floating menu
-- (built with nui.nvim, already a neo-tree dependency) offering "New
-- File", "New Folder", "Rename", "Move to...", "Delete", and "Cancel".
-- Left-click stays plain navigation throughout. "New File"/"New Folder"
-- create inside the clicked directory (or the clicked file's parent
-- directory) and accept a nested "sub/dir/name" path, creating
-- intermediate directories as needed. Choosing "Move to..." arms move
-- mode; the very next left-click anywhere in the tree is taken as the
-- destination (a folder click moves into it, a file click moves into its
-- parent folder) and prompts to confirm before touching disk. Clicking
-- the node you started from is a no-op and silently disarms. Choosing
-- "Rename" prompts for a new name in place (VS Code's F2 behavior).
-- Choosing "Delete" prompts to confirm, then removes the file/folder from
-- disk (and stages the removal in git when it was tracked). Applies
-- globally to every neo-tree sidebar in every codebase, since this lives
-- in the shared config.
local neotree_move = { source = nil, menu = nil }

local function neotree_node_at_cursor()
  local ok, manager = pcall(require, "neo-tree.sources.manager")
  if not ok then return nil end
  local state = manager.get_state("filesystem")
  if not state or not state.tree then return nil end
  local ok2, node = pcall(function() return state.tree:get_node() end)
  if not ok2 then return nil end
  return node
end

-- Places the cursor at the current mouse position, mirroring what a plain
-- click would do, so get_node() above resolves the row actually clicked.
local function neotree_set_cursor_from_mouse(winid)
  local pos = vim.fn.getmousepos()
  if pos.winid == 0 or pos.winid ~= winid then
    return false -- click landed outside the target window
  end
  return pcall(vim.api.nvim_win_set_cursor, pos.winid, { pos.line, 0 })
end

local function neotree_close_stray_menu()
  if neotree_move.menu then
    pcall(function() neotree_move.menu:unmount() end)
    neotree_move.menu = nil
  end
end

local function neotree_perform_move(source, target)
  local dest_dir = target.type == "directory"
    and target.path
    or vim.fn.fnamemodify(target.path, ":h")
  local basename = vim.fn.fnamemodify(source, ":t")
  local dest = dest_dir .. "/" .. basename

  if dest == source then
    return -- clicked the same node you started from: silent cancel
  end

  local proceed = vim.fn.confirm(
    ("Move %s to %s?"):format(basename, dest_dir), "&Yes\n&No", 2)
  if proceed ~= 1 then return end

  -- `git mv` refuses a file that is inside a repo but not yet tracked
  -- ("not under version control"), so check trackedness itself, not just
  -- repo membership, before choosing git mv over a plain rename.
  local parent = vim.fn.fnamemodify(source, ":h")
  vim.fn.system({ "git", "-C", parent, "rev-parse", "--is-inside-work-tree" })
  local inside_repo = vim.v.shell_error == 0

  local tracked = false
  if inside_repo then
    vim.fn.system({ "git", "-C", parent, "ls-files", "--error-unmatch", basename })
    tracked = vim.v.shell_error == 0
  end

  local ok_move, err
  if tracked then
    local out = vim.fn.system({ "git", "-C", parent, "mv", source, dest })
    ok_move, err = vim.v.shell_error == 0, out
  else
    local succ, msg = os.rename(source, dest)
    ok_move, err = succ ~= nil, msg
  end

  if not ok_move then
    vim.notify("neo-tree move failed: " .. (err or "unknown error"), vim.log.levels.ERROR)
    return
  end

  require("neo-tree.sources.manager").refresh("filesystem")
  vim.notify(("moved %s -> %s"):format(basename, dest_dir))
end

local function neotree_perform_create(node, is_dir)
  local target_dir = node.type == "directory"
    and node.path
    or vim.fn.fnamemodify(node.path, ":h")

  local kind = is_dir and "folder" or "file"
  local name = vim.fn.input(("New %s in %s: "):format(kind, vim.fn.fnamemodify(target_dir, ":t")), "")
  if name == "" then
    return -- cancelled: silent no-op
  end

  local dest = target_dir .. "/" .. name
  if vim.fn.filereadable(dest) == 1 or vim.fn.isdirectory(dest) == 1 then
    vim.notify("neo-tree create failed: " .. name .. " already exists", vim.log.levels.ERROR)
    return
  end

  local ok_create
  if is_dir then
    ok_create = vim.fn.mkdir(dest, "p") == 1
  else
    -- Support a nested "sub/dir/name" path by creating intermediate
    -- directories first, mirroring how VS Code's inline "New File" input
    -- treats a typed slash.
    vim.fn.mkdir(vim.fn.fnamemodify(dest, ":h"), "p")
    ok_create = vim.fn.writefile({}, dest) == 0
  end

  if not ok_create then
    vim.notify("neo-tree create failed: " .. name, vim.log.levels.ERROR)
    return
  end

  require("neo-tree.sources.manager").refresh("filesystem")
  vim.notify(("created %s"):format(name))
end

local function neotree_perform_rename(node)
  local path = node.path
  local basename = vim.fn.fnamemodify(path, ":t")
  local parent = vim.fn.fnamemodify(path, ":h")

  local new_name = vim.fn.input("Rename " .. basename .. " to: ", basename)
  if new_name == "" or new_name == basename then
    return -- cancelled or unchanged: silent no-op
  end

  local dest = parent .. "/" .. new_name
  if vim.fn.filereadable(dest) == 1 or vim.fn.isdirectory(dest) == 1 then
    vim.notify("neo-tree rename failed: " .. new_name .. " already exists", vim.log.levels.ERROR)
    return
  end

  -- Same trackedness check as move: `git mv` refuses a file that is inside
  -- a repo but not yet tracked ("not under version control").
  vim.fn.system({ "git", "-C", parent, "rev-parse", "--is-inside-work-tree" })
  local inside_repo = vim.v.shell_error == 0

  local tracked = false
  if inside_repo then
    vim.fn.system({ "git", "-C", parent, "ls-files", "--error-unmatch", basename })
    tracked = vim.v.shell_error == 0
  end

  local ok_rename, err
  if tracked then
    local out = vim.fn.system({ "git", "-C", parent, "mv", basename, new_name })
    ok_rename, err = vim.v.shell_error == 0, out
  else
    local succ, msg = os.rename(path, dest)
    ok_rename, err = succ ~= nil, msg
  end

  if not ok_rename then
    vim.notify("neo-tree rename failed: " .. (err or "unknown error"), vim.log.levels.ERROR)
    return
  end

  require("neo-tree.sources.manager").refresh("filesystem")
  vim.notify(("renamed %s -> %s"):format(basename, new_name))
end

local function neotree_perform_delete(node)
  local path = node.path
  local basename = vim.fn.fnamemodify(path, ":t")
  local is_dir = node.type == "directory"

  local proceed = vim.fn.confirm(
    ("Delete %s%s? This cannot be undone."):format(basename, is_dir and "/" or ""),
    "&Yes\n&No", 2)
  if proceed ~= 1 then return end

  local parent = vim.fn.fnamemodify(path, ":h")
  vim.fn.system({ "git", "-C", parent, "rev-parse", "--is-inside-work-tree" })
  local inside_repo = vim.v.shell_error == 0

  local rc = vim.fn.delete(path, is_dir and "rf" or "")
  if rc ~= 0 then
    vim.notify("neo-tree delete failed: " .. basename, vim.log.levels.ERROR)
    return
  end

  if inside_repo then
    -- Best-effort: stage the removal so `git status` reflects it when the
    -- path was tracked; a silent no-op when it never was.
    vim.fn.system({ "git", "-C", parent, "add", "-A", "--", basename })
  end

  require("neo-tree.sources.manager").refresh("filesystem")
  vim.notify(("deleted %s"):format(basename))
end

local function neotree_open_menu(node)
  local Menu = require("nui.menu")
  local menu = Menu({
    relative = "cursor",
    position = { row = 1, col = 0 },
    border = {
      style = "rounded",
      text = { top = " " .. vim.fn.fnamemodify(node.path, ":t") .. " ", top_align = "center" },
    },
    win_options = { winhighlight = "Normal:Normal,FloatBorder:FloatBorder" },
  }, {
    lines = {
      Menu.item("New File"),
      Menu.item("New Folder"),
      Menu.item("Rename"),
      Menu.item("Move to..."),
      Menu.item("Delete"),
      Menu.item("Cancel"),
    },
    max_width = 24,
    on_submit = function(item)
      neotree_move.menu = nil
      if item.text == "New File" then
        neotree_perform_create(node, false)
      elseif item.text == "New Folder" then
        neotree_perform_create(node, true)
      elseif item.text == "Rename" then
        neotree_perform_rename(node)
      elseif item.text == "Move to..." then
        neotree_move.source = node.path
        vim.notify(("move armed: click a destination for %s (click it again to cancel)")
          :format(vim.fn.fnamemodify(node.path, ":t")))
      elseif item.text == "Delete" then
        neotree_perform_delete(node)
      end
    end,
    on_close = function() neotree_move.menu = nil end,
  })

  neotree_move.menu = menu
  menu:mount()

  -- The menu's own default keymap only handles keyboard (j/k/<CR>/<Esc>);
  -- add a click-to-select so a mouse click on an item works the same way.
  vim.keymap.set("n", "<LeftMouse>", function()
    local pos = vim.fn.getmousepos()
    if pos.winid ~= menu.winid then
      -- clicked outside the menu: dismiss it and land the click where it
      -- was actually aimed, so the next click behaves normally.
      neotree_close_stray_menu()
      if pos.winid ~= 0 then
        pcall(vim.api.nvim_set_current_win, pos.winid)
        pcall(vim.api.nvim_win_set_cursor, pos.winid, { pos.line, 0 })
      end
      return
    end
    pcall(vim.api.nvim_win_set_cursor, menu.winid, { pos.line, 0 })
    menu.menu_props.on_submit()
  end, { buffer = menu.bufnr })
end

vim.api.nvim_create_autocmd("FileType", {
  pattern = "neo-tree",
  callback = function(ev)
    -- Left-click stays normal navigation, except when a move is armed, in
    -- which case the next left-click is taken as the destination.
    vim.keymap.set("n", "<LeftMouse>", function()
      neotree_close_stray_menu()
      if not neotree_set_cursor_from_mouse(vim.api.nvim_get_current_win()) then return end
      if not neotree_move.source then return end -- unarmed: plain click, cursor already moved above

      local node = neotree_node_at_cursor()
      if not node then return end
      local source = neotree_move.source
      neotree_move.source = nil
      neotree_perform_move(source, node)
    end, { buffer = ev.buf, desc = "neo-tree: pick move destination when armed" })

    -- Right-click opens the "Move to..." context menu.
    vim.keymap.set("n", "<RightMouse>", function()
      neotree_close_stray_menu()
      if not neotree_set_cursor_from_mouse(vim.api.nvim_get_current_win()) then return end
      local node = neotree_node_at_cursor()
      if not node then return end
      neotree_open_menu(node)
    end, { buffer = ev.buf, desc = "neo-tree: open move-to menu" })
  end,
})
