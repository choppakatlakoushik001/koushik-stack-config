#!/usr/bin/env python3
"""leakscan - Local secret and personal data scanner.

Enforces the data-stays-local rule on worker git diffs, paths, and outbound commands.
Single-file standard library implementation.
"""

import collections
import datetime
import fnmatch
import hashlib
import json
import math
import os
import re
import subprocess
import sys


LOCKFILE_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pipfile.lock",
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
    "go.sum",
    "flake.lock",
}

PASTE_DOMAINS = [
    "pastebin.com",
    "hastebin.com",
    "paste.ee",
    "ix.io",
    "dpaste.org",
    "dpaste.com",
    "termbin.com",
    "clbin.com",
    "paste.debian.net",
    "ghostbin.com",
    "0x0.st",
    "paste.centos.org",
]

STANDARD_ENV_KEYS = {
    "PATH",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "HOME",
    "USER",
    "SHELL",
    "PWD",
    "HOSTNAME",
    "EDITOR",
    "PAGER",
    "DISPLAY",
    "TMPDIR",
    "TEMP",
    "TMP",
}

NON_SECRET_UNQUOTED = {
    "none",
    "null",
    "nil",
    "true",
    "false",
    "undefined",
    "string",
    "boolean",
    "default",
    "optional",
    "required",
}

RULES = {
    "aws-key": {
        "regex": [
            re.compile(r"\b(AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
            re.compile(r"(?i)\baws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"),
        ],
        "severity": "high",
    },
    "gcp-key": {
        "regex": [
            re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
        ],
        "severity": "high",
    },
    "github-token": {
        "regex": [
            re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b"),
            re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"),
        ],
        "severity": "high",
    },
    "openai-key": {
        "regex": [
            re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}\b"),
        ],
        "severity": "high",
    },
    "anthropic-key": {
        "regex": [
            re.compile(r"\bsk-ant-(?:api[0-9]{2}-|admin-)?[A-Za-z0-9_\-]{20,}\b"),
        ],
        "severity": "high",
    },
    "xai-key": {
        "regex": [
            re.compile(r"\bxai-[A-Za-z0-9_\-]{20,}\b"),
        ],
        "severity": "high",
    },
    "slack-token": {
        "regex": [
            re.compile(r"\bxox[baprs]-[0-9a-zA-Z\-]{10,}\b"),
        ],
        "severity": "high",
    },
    "private-key": {
        "regex": [
            re.compile(r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY(?: BLOCK)?-----"),
        ],
        "severity": "high",
    },
    "jwt-token": {
        "regex": [
            re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
        ],
        "severity": "high",
    },
    "email-address": {
        "regex": [
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        ],
        "severity": "medium",
    },
    "phone-number": {
        "regex": [
            re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        ],
        "severity": "medium",
    },
}

CREDENTIAL_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|secret|api_key|apikey|auth_token)\s*[:=]\s*(['\"][^'\"]{4,}['\"]|[^\s,;'\"\)\]\}]{6,})"
)

DOTENV_ASSIGN_RE = re.compile(r"^(?:export\s+)?([A-Z0-9_]{3,})=([^\s].*)$")
ENV_STYLE_SUFFIXES = (".env", ".ini", ".cfg", ".conf", ".properties")


def is_env_style_file(path: str) -> bool:
    """Check if file is an environment or configuration file."""
    basename = os.path.basename(path).lower()
    if basename.startswith(".env"):
        return True
    return any(basename.endswith(suffix) for suffix in ENV_STYLE_SUFFIXES)


class Finding:
    def __init__(
        self,
        source: str,
        rule: str,
        severity: str,
        path: str,
        line: int,
        match: str,
    ):
        self.source = source
        self.rule = rule
        self.severity = severity
        self.path = path
        self.line = line
        self.match = match

    def __repr__(self):
        return f"<Finding {self.rule} {self.path}:{self.line}>"


class AllowlistEntry:
    def __init__(self, rule: str, path_glob: str, reason: str):
        self.rule = rule
        self.path_glob = path_glob
        self.reason = reason


def calculate_entropy(s: str) -> float:
    """Calculate Shannon entropy in bits per character."""
    if not s:
        return 0.0
    length = len(s)
    counts = collections.Counter(s)
    entropy = 0.0
    for count in counts.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def is_lockfile(path: str) -> bool:
    """Check if file is a dependency lockfile where hashes are expected."""
    basename = os.path.basename(path).lower()
    return basename in LOCKFILE_NAMES or basename.endswith(".lock")


def is_hex_git_hash(token: str) -> bool:
    """Check if token is a hex git commit hash (40 or 64 hex characters)."""
    return len(token) in (40, 64) and bool(re.fullmatch(r"[0-9a-fA-F]+", token))


def get_salt(salt_override: str | None = None) -> str:
    """Retrieve or create salt for hashing findings digests."""
    if salt_override:
        return salt_override
    env_salt = os.environ.get("LEAKSCAN_SALT")
    if env_salt:
        return env_salt
    salt_file = os.path.expanduser("~/.config/leakscan/salt")
    if os.path.exists(salt_file):
        try:
            with open(salt_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass
    # Stable default salt fallback
    return "leakscan-default-salt-v1"


def compute_digest(match: str, salt: str) -> str:
    """Return first 12 hex chars of sha256(salt + match)."""
    data = (salt + match).encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:12]


def load_personal_patterns(path: str | None = None) -> list[str]:
    """Load literal personal patterns from private file."""
    if path is None:
        path = os.path.expanduser("~/.config/leakscan/personal.txt")
    if not os.path.exists(path):
        return []
    patterns = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except Exception:
        pass
    return patterns


def load_allowlist(allowlist_path: str) -> list[AllowlistEntry]:
    """Load allowlist rules from .leakscan-allow."""
    if not os.path.exists(allowlist_path):
        return []
    entries = []
    with open(allowlist_path, "r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=2)
            if len(parts) < 3 or not parts[2].strip():
                raise ValueError(
                    f".leakscan-allow line {line_no}: reason required for rule '{parts[0] if parts else ''}'"
                )
            entries.append(
                AllowlistEntry(
                    rule=parts[0],
                    path_glob=parts[1],
                    reason=parts[2].strip(),
                )
            )
    return entries


def is_allowlisted(finding: Finding, allowlist: list[AllowlistEntry]) -> bool:
    """Check if finding matches any allowlist entry with a reason."""
    for entry in allowlist:
        if entry.rule != "*" and entry.rule != finding.rule:
            continue
        if entry.path_glob == "*":
            return True
        if fnmatch.fnmatch(finding.path, entry.path_glob):
            return True
        if fnmatch.fnmatch(os.path.basename(finding.path), entry.path_glob):
            return True
        if fnmatch.fnmatch(os.path.normpath(finding.path), entry.path_glob):
            return True
    return False


def scan_credential_assignment(line: str) -> list[str]:
    """Detect password=/secret= assignments with hardcoded values."""
    matches = []
    for m in CREDENTIAL_ASSIGN_RE.finditer(line):
        val = m.group(2).strip()
        if (val.startswith("'") and val.endswith("'")) or (
            val.startswith('"') and val.endswith('"')
        ):
            inner = val[1:-1]
            if len(inner) >= 4:
                matches.append(val)
        else:
            val_lower = val.lower()
            if val_lower in NON_SECRET_UNQUOTED:
                continue
            if "." in val or "(" in val or ")" in val or "/" in val or "=" in val or val.isdigit():
                continue
            matches.append(val)
    return matches


def scan_dotenv_long_value(line: str, path: str) -> list[str]:
    """Detect .env-style KEY=value lines with long values."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return []
    m = DOTENV_ASSIGN_RE.match(stripped)
    if not m:
        return []
    key, raw_val = m.groups()
    if key in STANDARD_ENV_KEYS:
        return []

    # Strip optional quotes
    if (raw_val.startswith('"') and raw_val.endswith('"')) or (
        raw_val.startswith("'") and raw_val.endswith("'")
    ):
        val = raw_val[1:-1]
    else:
        val = raw_val

    # Single token without spaces or operators
    if not re.fullmatch(r"[A-Za-z0-9_\-\.@#+/=]+", val):
        return []

    if len(val) < 16:
        return []

    # Apply only in .env-style files unless value also passes high-entropy test
    if is_env_style_file(path):
        return [val]
    else:
        if calculate_entropy(val) >= 4.0:
            return [val]
        return []


def extract_candidate_tokens(line: str) -> list[str]:
    """Extract candidate high-entropy tokens from a line of text."""
    cleaned = re.sub(r"https?://\S+", " ", line)
    raw_pieces = re.split(r"[\s\"'`;,\(\)\[\]\{\}<>\.]+", cleaned)
    tokens: list[str] = []
    for piece in raw_pieces:
        p = piece.strip()
        if "=" in p and not p.endswith("="):
            for sub in p.split("="):
                sub_clean = sub.strip()
                if len(sub_clean) >= 20:
                    tokens.append(sub_clean)
            continue
        p = p.lstrip("=:")
        if len(p) >= 20:
            tokens.append(p)

    filtered: list[str] = []
    for t in tokens:
        if t.startswith("/") or t.startswith("./") or t.startswith("../"):
            continue
        if t.count("/") >= 2:
            continue
        if "$" in t:  # shell variable expansion, not a literal value
            continue
        if re.fullmatch(r"[A-Z0-9]*_[A-Z0-9_]*(:[-=?+].*)?", t):
            continue  # UPPER_SNAKE variable name, optionally with a shell default
        if re.fullmatch(r"[a-z0-9_/:]+", t) and t.count("_") >= 2:
            continue  # lower_snake identifier or test node id
        if not any(c.isdigit() for c in t) and any(c in t for c in "-_/"):
            continue  # word-like identifier or path (e.g. WATCHER_RECOVERY_PENDING)
        filtered.append(t)
    return filtered


def scan_line(
    line: str,
    path: str,
    line_num: int,
    source: str,
    personal_patterns: list[str] | None = None,
) -> list[Finding]:
    """Scan a single line of text across all detection rules."""
    findings: list[Finding] = []
    matched_spans: set[str] = set()

    # 1. Specific regex rules
    for rule_name, rule_def in RULES.items():
        for pattern in rule_def["regex"]:
            for m in pattern.finditer(line):
                matched_text = m.group(0)
                if rule_name == "phone-number" and matched_text.isdigit():
                    continue  # bare digit runs are timestamps or ids; real numbers carry separators or +
                findings.append(
                    Finding(
                        source=source,
                        rule=rule_name,
                        severity=rule_def["severity"],
                        path=path,
                        line=line_num,
                        match=matched_text,
                    )
                )
                matched_spans.add(matched_text)

    # 2. Credential assignments (password=, secret=)
    for cred in scan_credential_assignment(line):
        if not any(cred in span or span in cred for span in matched_spans):
            findings.append(
                Finding(
                    source=source,
                    rule="credential-assignment",
                    severity="high",
                    path=path,
                    line=line_num,
                    match=cred,
                )
            )
            matched_spans.add(cred)

    # 3. Dotenv long values
    for val in scan_dotenv_long_value(line, path):
        if not any(val in span or span in val for span in matched_spans):
            findings.append(
                Finding(
                    source=source,
                    rule="dotenv-long-value",
                    severity="high",
                    path=path,
                    line=line_num,
                    match=val,
                )
            )
            matched_spans.add(val)

    # 4. High-entropy tokens (length >= 20, entropy >= 4.0 bits/char)
    if not is_lockfile(path):
        candidate_tokens = extract_candidate_tokens(line)
        for token in candidate_tokens:
            if is_hex_git_hash(token):
                continue
            # Skip if token is part of an already matched rule
            if any(token in span or span in token for span in matched_spans):
                continue
            h = calculate_entropy(token)
            if h >= 4.0:
                findings.append(
                    Finding(
                        source=source,
                        rule="high-entropy-token",
                        severity="high",
                        path=path,
                        line=line_num,
                        match=token,
                    )
                )
                matched_spans.add(token)

    # 5. Personal patterns
    if personal_patterns:
        line_lower = line.lower()
        for pat in personal_patterns:
            if pat.lower() in line_lower:
                findings.append(
                    Finding(
                        source=source,
                        rule="personal-pattern",
                        severity="medium",
                        path=path,
                        line=line_num,
                        match=pat,
                    )
                )

    return findings


def parse_unified_diff(diff_text: str) -> list[tuple[str, int, str]]:
    """Parse unified git diff output into added (path, line_number, line_content)."""
    current_file = None
    current_line = 0
    added_lines: list[tuple[str, int, str]] = []

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            parts = raw_line.split()
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    current_file = b_path[2:]
                else:
                    current_file = b_path
        elif raw_line.startswith("+++ "):
            target = raw_line[4:].strip()
            if target == "/dev/null":
                current_file = None
            elif target.startswith("b/"):
                current_file = target[2:]
            else:
                current_file = target
        elif raw_line.startswith("@@ "):
            m = re.search(r"\+([0-9]+)(?:,([0-9]+))?", raw_line)
            if m:
                current_line = int(m.group(1))
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            if current_file:
                added_lines.append((current_file, current_line, raw_line[1:]))
            current_line += 1
        elif raw_line.startswith(" "):
            current_line += 1
        elif raw_line.startswith("-"):
            pass

    return added_lines


def scan_diff(
    git_dir: str,
    base: str,
    head: str,
    personal_patterns: list[str] | None = None,
) -> list[Finding]:
    """Scan added lines in git diff between base and head."""
    if not os.path.exists(git_dir):
        raise FileNotFoundError(f"git directory not found: {git_dir}")

    # If git_dir contains .git (as a directory or as a git-worktree file)
    git_dot = os.path.join(git_dir, ".git")
    if os.path.exists(git_dot):
        cmd = ["git", "-C", git_dir, "diff", "-U0", "--no-color", base, head]
    elif os.path.exists(os.path.join(git_dir, "HEAD")):
        # git_dir is directly a .git directory
        cmd = ["git", "--git-dir", git_dir, "diff", "-U0", "--no-color", base, head]
    else:
        cmd = ["git", "-C", git_dir, "diff", "-U0", "--no-color", base, head]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")

    added = parse_unified_diff(proc.stdout)
    findings: list[Finding] = []
    for file_path, line_no, content in added:
        findings.extend(
            scan_line(
                content,
                path=file_path,
                line_num=line_no,
                source="diff",
                personal_patterns=personal_patterns,
            )
        )
    return findings


def scan_file(
    filepath: str,
    personal_patterns: list[str] | None = None,
) -> list[Finding]:
    """Scan a single file line by line."""
    findings: list[Finding] = []
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(4096)
            if b"\0" in chunk:
                # Binary file, skip
                return []
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                findings.extend(
                    scan_line(
                        line,
                        path=filepath,
                        line_num=line_no,
                        source="paths",
                        personal_patterns=personal_patterns,
                    )
                )
    except Exception as e:
        sys.stderr.write(f"leakscan: warning: could not read {filepath}: {e}\n")
    return findings


def scan_paths(
    paths: list[str],
    personal_patterns: list[str] | None = None,
) -> list[Finding]:
    """Scan list of file or directory paths."""
    findings: list[Finding] = []
    for p in paths:
        if not os.path.exists(p):
            raise FileNotFoundError(f"path not found: {p}")
        if os.path.isfile(p):
            findings.extend(scan_file(p, personal_patterns))
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                if ".git" in dirs:
                    dirs.remove(".git")
                for file in files:
                    full_path = os.path.join(root, file)
                    findings.extend(scan_file(full_path, personal_patterns))
    return findings


def classify_command(
    argv: list[str],
    personal_patterns: list[str] | None = None,
) -> list[Finding]:
    """Classify outbound command and scan its arguments."""
    findings: list[Finding] = []
    if not argv:
        return findings

    cmd_str = " ".join(argv)
    binary = os.path.basename(argv[0]).lower()

    # Paste services check
    for arg in argv:
        arg_lower = arg.lower()
        for domain in PASTE_DOMAINS:
            if domain in arg_lower:
                findings.append(
                    Finding(
                        source="command",
                        rule="outbound-paste",
                        severity="high",
                        path="-",
                        line=0,
                        match=arg,
                    )
                )
                break
        if findings:
            break
    if binary == "pastebinit":
        findings.append(
            Finding(
                source="command",
                rule="outbound-paste",
                severity="high",
                path="-",
                line=0,
                match=cmd_str,
            )
        )

    # curl rules: upload, POST data, -T
    if binary == "curl":
        is_upload = False
        upload_flags = {
            "-d",
            "--data",
            "--data-raw",
            "--data-ascii",
            "--data-binary",
            "--data-urlencode",
            "--json",
            "-f",
            "--form",
            "--form-string",
            "-t",
            "--upload-file",
        }
        for i, arg in enumerate(argv[1:], start=1):
            arg_lower = arg.lower()
            if (
                arg_lower in upload_flags
                or any(arg_lower.startswith(f + "=") for f in upload_flags)
                or any(
                    arg_lower.startswith(f) and len(f) == 2 and len(arg_lower) > 2
                    for f in ("-d", "-f", "-t")
                )
            ):
                is_upload = True
                break
            if arg_lower in ("-x", "--request"):
                if i + 1 < len(argv) and argv[i + 1].upper() in (
                    "POST",
                    "PUT",
                    "PATCH",
                ):
                    is_upload = True
                    break
            if arg_lower.startswith("-x") and arg_lower[2:].upper() in (
                "POST",
                "PUT",
                "PATCH",
            ):
                is_upload = True
                break
            if arg_lower.startswith("--request=") and arg_lower.split("=", 1)[
                1
            ].upper() in ("POST", "PUT", "PATCH"):
                is_upload = True
                break
        if is_upload:
            findings.append(
                Finding(
                    source="command",
                    rule="outbound-curl",
                    severity="high",
                    path="-",
                    line=0,
                    match=cmd_str,
                )
            )

    # wget rules: upload, POST data
    elif binary == "wget":
        is_post = False
        for i, arg in enumerate(argv[1:], start=1):
            arg_lower = arg.lower()
            if arg_lower.startswith("--post-data") or arg_lower.startswith(
                "--post-file"
            ):
                is_post = True
                break
            if arg_lower.startswith("--method=") and arg_lower.split("=", 1)[
                1
            ].upper() in ("POST", "PUT", "PATCH"):
                is_post = True
                break
            if (
                arg_lower == "--method"
                and i + 1 < len(argv)
                and argv[i + 1].upper() in ("POST", "PUT", "PATCH")
            ):
                is_post = True
                break
        if is_post:
            findings.append(
                Finding(
                    source="command",
                    rule="outbound-wget",
                    severity="high",
                    path="-",
                    line=0,
                    match=cmd_str,
                )
            )

    # scp rule
    elif binary == "scp":
        findings.append(
            Finding(
                source="command",
                rule="outbound-scp",
                severity="high",
                path="-",
                line=0,
                match=cmd_str,
            )
        )

    # rsync to host:
    elif binary == "rsync":
        has_remote = False
        for arg in argv[1:]:
            if arg.startswith("-"):
                continue
            if ":" in arg and not arg.startswith(":"):
                has_remote = True
                break
        if has_remote:
            findings.append(
                Finding(
                    source="command",
                    rule="outbound-rsync",
                    severity="high",
                    path="-",
                    line=0,
                    match=cmd_str,
                )
            )

    # git push
    elif binary == "git":
        subcmd = None
        i = 1
        while i < len(argv):
            arg = argv[i]
            if arg in ("-C", "-c", "--git-dir", "--work-tree"):
                i += 2
                continue
            if arg.startswith("-"):
                i += 1
                continue
            subcmd = arg
            break
        if subcmd == "push":
            findings.append(
                Finding(
                    source="command",
                    rule="outbound-git-push",
                    severity="high",
                    path="-",
                    line=0,
                    match=cmd_str,
                )
            )

    # gh gist
    elif binary == "gh":
        subcmd = None
        for arg in argv[1:]:
            if not arg.startswith("-"):
                subcmd = arg
                break
        if subcmd == "gist":
            findings.append(
                Finding(
                    source="command",
                    rule="outbound-gh-gist",
                    severity="high",
                    path="-",
                    line=0,
                    match=cmd_str,
                )
            )

    # cloud copy/sync: aws, gsutil, gcloud, rclone
    elif binary == "aws":
        args_lower = [a.lower() for a in argv[1:]]
        if "s3" in args_lower or "s3api" in args_lower:
            actions = {"cp", "sync", "mv", "put-object"}
            if any(act in args_lower for act in actions):
                findings.append(
                    Finding(
                        source="command",
                        rule="outbound-cloud-copy",
                        severity="high",
                        path="-",
                        line=0,
                        match=cmd_str,
                    )
                )
    elif binary == "gsutil":
        args_lower = [a.lower() for a in argv[1:]]
        if any(act in args_lower for act in ("cp", "rsync", "mv")):
            findings.append(
                Finding(
                    source="command",
                    rule="outbound-cloud-copy",
                    severity="high",
                    path="-",
                    line=0,
                    match=cmd_str,
                )
            )
    elif binary == "gcloud":
        args_lower = [a.lower() for a in argv[1:]]
        if "storage" in args_lower and any(
            act in args_lower for act in ("cp", "rsync", "mv")
        ):
            findings.append(
                Finding(
                    source="command",
                    rule="outbound-cloud-copy",
                    severity="high",
                    path="-",
                    line=0,
                    match=cmd_str,
                )
            )
    elif binary == "rclone":
        args_lower = [a.lower() for a in argv[1:]]
        if any(
            act in args_lower
            for act in ("copy", "sync", "move", "copyto", "moveto")
        ):
            findings.append(
                Finding(
                    source="command",
                    rule="outbound-cloud-copy",
                    severity="high",
                    path="-",
                    line=0,
                    match=cmd_str,
                )
            )

    # nc to remote
    elif binary in ("nc", "netcat", "ncat"):
        args_lower = [a.lower() for a in argv[1:]]
        is_listen = any(
            arg in ("-l", "--listen") or (arg.startswith("-l") and len(arg) > 1)
            for arg in args_lower
        )
        is_unix = "-u" in args_lower
        if not is_listen and not is_unix:
            pos_args = []
            skip_next = False
            for arg in argv[1:]:
                if skip_next:
                    skip_next = False
                    continue
                if arg in ("-w", "-p", "-s", "-i", "-b"):
                    skip_next = True
                    continue
                if arg.startswith("-"):
                    continue
                pos_args.append(arg)
            if pos_args:
                host = pos_args[0].lower()
                if host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
                    findings.append(
                        Finding(
                            source="command",
                            rule="outbound-nc",
                            severity="high",
                            path="-",
                            line=0,
                            match=cmd_str,
                        )
                    )

    # Also scan command string itself for secret tokens/patterns
    secret_findings = scan_line(
        cmd_str,
        path="-",
        line_num=0,
        source="command",
        personal_patterns=personal_patterns,
    )
    findings.extend(secret_findings)

    return findings


def log_findings(
    findings: list[Finding],
    findings_log_path: str,
    tool: str,
    salt: str,
):
    """Append findings to log file in JSON lines format."""
    if not findings:
        return
    now_iso = (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    log_dir = os.path.dirname(os.path.abspath(findings_log_path))
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    with open(findings_log_path, "a", encoding="utf-8") as f:
        for finding in findings:
            digest = compute_digest(finding.match, salt)
            record = {
                "at": now_iso,
                "tool": tool,
                "source": finding.source,
                "rule": finding.rule,
                "severity": finding.severity,
                "path": finding.path,
                "line": finding.line,
                "digest": digest,
            }
            f.write(json.dumps(record) + "\n")


def print_human_findings(findings: list[Finding], salt: str):
    """Print human output: path:line: rule digest only."""
    for finding in findings:
        digest = compute_digest(finding.match, salt)
        print(f"{finding.path}:{finding.line}: {finding.rule} {digest}")


def main(raw_args: list[str] | None = None) -> int:
    if raw_args is None:
        raw_args = sys.argv[1:]

    # Parse global options
    findings_log = os.environ.get(
        "LEAKSCAN_FINDINGS_PATH",
        os.path.expanduser("~/local-llm/leakscan/findings.jsonl"),
    )
    allowlist_path = ".leakscan-allow"
    personal_file = os.path.expanduser("~/.config/leakscan/personal.txt")
    salt = None
    tool = os.environ.get("LEAKSCAN_TOOL", "unknown")

    idx = 0
    while idx < len(raw_args):
        arg = raw_args[idx]
        if arg == "--findings-log" and idx + 1 < len(raw_args):
            findings_log = raw_args[idx + 1]
            idx += 2
        elif arg == "--allowlist" and idx + 1 < len(raw_args):
            allowlist_path = raw_args[idx + 1]
            idx += 2
        elif arg == "--personal-file" and idx + 1 < len(raw_args):
            personal_file = raw_args[idx + 1]
            idx += 2
        elif arg == "--salt" and idx + 1 < len(raw_args):
            salt = raw_args[idx + 1]
            idx += 2
        elif arg == "--tool" and idx + 1 < len(raw_args):
            tool = raw_args[idx + 1]
            idx += 2
        else:
            break

    remaining = raw_args[idx:]
    if not remaining:
        sys.stderr.write(
            "usage: leakscan.py [options] <diff|paths|command> [args...]\n"
        )
        return 2

    subcmd = remaining[0]
    salt_val = get_salt(salt)

    try:
        personal_patterns = load_personal_patterns(personal_file)
        findings: list[Finding] = []

        if subcmd == "diff":
            if len(remaining) != 4:
                sys.stderr.write(
                    "usage: leakscan.py diff <git-dir> <base> <head>\n"
                )
                return 2
            git_dir, base, head = remaining[1], remaining[2], remaining[3]
            # If allowlist not found in cwd, check git_dir
            if not os.path.exists(allowlist_path) and os.path.exists(
                os.path.join(git_dir, ".leakscan-allow")
            ):
                allowlist_path = os.path.join(git_dir, ".leakscan-allow")
            findings = scan_diff(
                git_dir, base, head, personal_patterns=personal_patterns
            )

        elif subcmd == "paths":
            if len(remaining) < 2:
                sys.stderr.write("usage: leakscan.py paths <path>...\n")
                return 2
            findings = scan_paths(
                remaining[1:], personal_patterns=personal_patterns
            )

        elif subcmd == "command":
            cmd_args = remaining[1:]
            if cmd_args and cmd_args[0] == "--":
                cmd_args = cmd_args[1:]
            if not cmd_args:
                sys.stderr.write("usage: leakscan.py command -- <argv...>\n")
                return 2
            findings = classify_command(
                cmd_args, personal_patterns=personal_patterns
            )

        else:
            sys.stderr.write(f"leakscan: error: unknown subcommand: {subcmd}\n")
            return 2

        # Allowlist filtering
        allowlist = load_allowlist(allowlist_path)
        active_findings = [f for f in findings if not is_allowlisted(f, allowlist)]

        if active_findings:
            log_findings(active_findings, findings_log, tool, salt_val)
            print_human_findings(active_findings, salt_val)
            return 1

        return 0

    except ValueError as ve:
        # Invalid allowlist or format error
        sys.stderr.write(f"leakscan: error: {ve}\n")
        return 2
    except (FileNotFoundError, RuntimeError) as fe:
        sys.stderr.write(f"leakscan: error: {fe}\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"leakscan: internal error: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
