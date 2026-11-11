#!/usr/bin/env python3
"""Conservative, approval-gated local cleanup. Python 3 standard library only."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

VERSION = '1.0.0'
WEEK = 7 * 86400
CATEGORIES = ('cuda-toolkit', 'bench-logs', 'go-build', 'firstmate-temp', 'tmp')
ENABLED = {'go-build'}
GO_ENTRY = re.compile(r'[0-9a-f]{64}-[ad]')
ALLOWED_SAME_UID_UNREADABLE = frozenset({
    'ssh-agent',
    'gpg-agent',
    'gnome-keyring-daemon',
    'keyboxd',
    'secret-tool',
    # Per-user session manager and PAM helper (non-dumpable because PAM starts them; they never open cache files).
    'systemd',
    '(sd-pam)',
})


def human_bytes(size: int) -> str:
    """Qwen draft, corrected for bool, bytes and values beyond PiB."""
    if type(size) is not int or size < 0:
        raise ValueError('size must be a non-negative integer')
    units = ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB')
    value = size
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f'{size} B' if index == 0 else f'{value:.1f} {units[index]}'


def run(args, timeout=20, **kwargs):
    """Bounded commands, without shell interpolation or interactive input."""
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout, check=False, **kwargs)


def under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


@dataclass(frozen=True)
class Layout:
    home: Path
    temp: Path
    approvals: Path
    proc: Path = Path('/proc')

    @property
    def local(self):
        return self.home / 'local-llm'

    @property
    def firstmate(self):
        return self.home / 'kunchen/crew/kunchen_firstmate'

    @property
    def roots(self):
        return {'cuda-toolkit': self.local / 'tools/cuda-13.1',
                'bench-logs': self.local / 'bench-logs',
                'go-build': self.home / '.cache/go-build',
                'firstmate-temp': self.firstmate / 'state', 'tmp': self.temp}


class Refusal(Exception):
    pass


def no_symlinks(path: Path):
    """Check each existing component; resolve() alone would hide symlinks."""
    for part in reversed((path, *path.parents)):
        if part.is_symlink():
            raise Refusal('symlink in path')


def python_related(path: Path) -> bool:
    for part in path.parts:
        p = part.lower()
        if (re.search(r'python|(^|[._-])(pip\d*|uv|venv|virtualenvs?|site-packages|dist-packages|pycache)([._-]|$)', p)
                or p.endswith(('.py', '.pyc', '.pyo', '.pyi', '.whl', '.egg'))
                or p in {'pyvenv.cfg', 'pyproject.toml', 'pipfile', 'pipfile.lock',
                         'poetry.lock', 'uv.lock', 'setup.cfg', 'tox.ini', '.pypirc'}
                or p.startswith(('requirements', 'conda', 'miniconda', 'anaconda'))):
            return True
    return False


def guard(path: Path, category: str, layout: Layout):
    """Deny first, then allow only a regular, singly linked file in one rule root."""
    path = Path(os.path.abspath(path))
    if category not in CATEGORIES:
        raise Refusal('unknown category')
    no_symlinks(path)
    if python_related(path):
        raise Refusal('Python-related path')
    if any('qwen' in p.lower() or p.lower().endswith(('.gguf', '.safetensors'))
           for p in path.parts):
        raise Refusal('model files')
    protected = (layout.home / '.treehouse', layout.home / '.local/share/job-pilot',
                 layout.home / '.cache/ms-playwright', layout.local / 'tools/python',
                 layout.local / 'tools/pip-cache', layout.home / '.cache/pip',
                 layout.home / '.cache/uv', layout.local / 'models')
    if any(under(path, p) for p in protected):
        raise Refusal('protected data')
    if under(path, layout.firstmate):
        if not (category == 'firstmate-temp' and path.parent == layout.roots[category]
                and re.fullmatch(r'\.claude-autoarm-output\.[A-Za-z0-9]+', path.name)):
            raise Refusal('Firstmate home')
    if category == 'go-build' and layout.roots[category] != layout.home / '.cache/go-build':
        raise Refusal('Go cache root is fixed; personal data is protected')
    if not under(path, layout.roots[category]) or path == layout.roots[category]:
        raise Refusal('outside the category root (personal data is protected)')
    for parent in (path.parent, *path.parents):
        if (parent / '.git').exists() or (parent / '.git').is_symlink():
            raise Refusal('inside a Git repository or worktree')
        # Bare repositories have no .git marker.
        if (parent / 'HEAD').is_file() and (parent / 'objects').is_dir() and (parent / 'refs').is_dir():
            raise Refusal('inside a bare Git repository')
        if (parent / 'pyvenv.cfg').exists() or (parent / 'conda-meta').is_dir():
            raise Refusal('Python environment')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise Refusal('not a regular file')
    if info.st_uid != os.getuid():
        raise Refusal('not owned by current user')
    if info.st_nlink != 1:
        raise Refusal('hard-linked file')
    if category == 'go-build':
        relative = path.relative_to(layout.roots[category])
        if (len(relative.parts) != 2 or not re.fullmatch('[0-9a-f]{2}', relative.parts[0])
                or not GO_ENTRY.fullmatch(path.name) or not path.name.startswith(relative.parts[0])):
            raise Refusal('not a recognized Go cache entry')
    return info


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass
class Processes:
    paths: set[Path] = field(default_factory=set)
    commands: list[str] = field(default_factory=list)
    biggest: list[dict] = field(default_factory=list)
    uncertain: int = 0

    @classmethod
    def read(cls, proc: Path):
        """Read kernel process metadata only; never emit command-line arguments."""
        result = cls()
        try:
            entries = list(proc.iterdir())
        except OSError:
            result.uncertain += 1
            return result
        for entry in entries:
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                raw_stat = (entry / 'stat').read_text()
                tail = raw_stat[raw_stat.rindex(')') + 2:].split()
                status = (entry / 'status').read_text()
                rss = re.search(r'^VmRSS:\s+(\d+)', status, re.M)
                comm = raw_stat[raw_stat.index('(') + 1:raw_stat.rindex(')')]
                ppid, tty = int(tail[1]), int(tail[4])
                cpu = int(tail[11]) + int(tail[12])
                age_ticks = float((proc / 'uptime').read_text().split()[0]) * os.sysconf('SC_CLK_TCK') - int(tail[19])
                parent_gone = ppid == 1 or not (proc / str(ppid)).exists()
                # Lifetime CPU fraction is a heuristic, not proof of current idleness.
                possible = parent_gone and tty == 0 and age_ticks > 3600 * os.sysconf('SC_CLK_TCK') and cpu / max(age_ticks, 1) < .001
                result.biggest.append({'pid': int(entry.name), 'name': comm,
                                       'rss': int(rss[1]) * 1024 if rss else 0,
                                       'possible_orphan': possible})
                cmd = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
                result.commands.append(cmd)
                for key in ('cwd', 'exe', 'root'):
                    try:
                        target = os.readlink(entry / key)
                    except FileNotFoundError:
                        continue
                    if target.startswith('/'):
                        result.paths.add(Path(target.removesuffix(' (deleted)')))
                for fd in (entry / 'fd').iterdir():
                    try:
                        target = os.readlink(fd)
                    except FileNotFoundError:
                        continue
                    if target.startswith('/'):
                        result.paths.add(Path(target.removesuffix(' (deleted)')))
                for line in (entry / 'maps').read_text().splitlines():
                    pieces = line.split(None, 5)
                    if len(pieces) == 6 and pieces[5].startswith('/'):
                        result.paths.add(Path(pieces[5].removesuffix(' (deleted)')))
            except FileNotFoundError:
                # An exited process is harmless; a still-live inaccessible process isn't.
                if entry.exists():
                    cls._record_unreadable(entry, result)
            except (OSError, ValueError, IndexError):
                cls._record_unreadable(entry, result)
        result.biggest.sort(key=lambda p: p['rss'], reverse=True)
        return result

    @classmethod
    def _record_unreadable(cls, entry: Path, result: Processes):
        try:
            status_text = (entry / 'status').read_text()
        except OSError:
            status_text = ''
        uid_match = re.search(r'^Uid:\s*(\d+)', status_text, re.M)
        if uid_match:
            proc_uid = int(uid_match.group(1))
        else:
            try:
                proc_uid = entry.stat().st_uid
            except OSError:
                proc_uid = None

        current_uid = os.getuid()
        # Other-uid processes cannot access user-owned caches (mode 0700/0600)
        # without elevated privileges and do not block deletion.
        if proc_uid is not None and proc_uid != current_uid:
            return

        # For same-uid unreadable processes, check allowlist of known auth/credential daemons.
        name_match = re.search(r'^Name:\s*(\S+)', status_text, re.M)
        if name_match and name_match.group(1) in ALLOWED_SAME_UID_UNREADABLE:
            return

        result.uncertain += 1

    def unused(self, path):
        if self.uncertain:
            raise Refusal(f'incomplete process visibility ({self.uncertain} processes)')
        if path in self.paths or any(str(path) in cmd for cmd in self.commands):
            raise Refusal('live process references file')
        cache = path.parent.parent
        if any(under(p, cache) for p in self.paths) or any(str(cache) in cmd for cmd in self.commands):
            raise Refusal('live process references cache directory')
        # A running Go producer can use cache entries between handle checks.
        if any(re.search(r'(^|[/\s])(go|compile|link)(\s|$)', cmd) for cmd in self.commands):
            raise Refusal('Go producer may be active')


def check_handles(path, runner=run):
    """Both tools must conclusively find no holder. Missing tools/errors refuse."""
    lsof = runner(['lsof', '-nP', '-F', 'p', '--', str(path)])
    if lsof.returncode != 1 or lsof.stdout.strip() or lsof.stderr.strip():
        raise Refusal('open handle or inconclusive lsof check')
    # psmisc fuser rejects a '--' separator (prints usage, which read as
    # "inconclusive" and refused every file); an absolute path cannot be
    # mistaken for an option, so pass it without one.
    if not str(path).startswith('/'):
        raise Refusal('non-absolute path for fuser check')
    fuser = runner(['fuser', '-s', str(path)])
    if fuser.returncode != 1 or fuser.stdout.strip() or fuser.stderr.strip():
        raise Refusal('open handle or inconclusive fuser check')


def measure(root: Path):
    """Allocated bytes, no symlink traversal, no descent into protected subtrees."""
    total, files, errors = 0, 0, 0
    if not root.exists():
        return total, files, errors
    if root.is_symlink():
        return 0, 0, 1
    def onerror(_):
        nonlocal errors
        errors += 1
    for base, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()
                   and d not in {'.git', '.treehouse'}]
        for name in names:
            try:
                info = (Path(base) / name).lstat()
                if stat.S_ISREG(info.st_mode):
                    total += info.st_blocks * 512
                    files += 1
            except OSError:
                errors += 1
    return total, files, errors


@dataclass
class Category:
    name: str
    size: int = 0
    count: int = 0
    reason: str = ''
    candidates: list = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)

    @property
    def eligible_bytes(self):
        return sum(item[1].st_blocks * 512 for item in self.candidates)


def approvals_read(layout):
    path = layout.approvals
    no_symlinks(path)
    if not path.exists():
        return {}
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise Refusal('unsafe approved.json')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('approved'), dict):
        raise Refusal('invalid approved.json schema')
    if any(k not in ENABLED or type(v) is not int or v <= 0 for k, v in data['approved'].items()):
        raise Refusal('invalid category approval')
    return data['approved']


def cuda_evidence(layout, runner=run):
    """Collect bounded positive reference evidence; absence never enables deletion."""
    root = str(layout.roots['cuda-toolkit'])
    source = layout.local / 'llama.cpp/src'
    builds = sorted(p for p in source.glob('build*') if p.is_dir() and not p.is_symlink())
    configs = libraries = scripts = errors = 0
    for build in builds:
        cache = build / 'CMakeCache.txt'
        if cache.is_file():
            try:
                configs += sum(root in line for line in cache.read_text().splitlines())
            except OSError:
                errors += 1
        for name in ('llama-server', 'llama-cli', 'llama-bench'):
            binary = build / 'bin' / name
            if not binary.is_file() or binary.is_symlink():
                continue
            try:
                with binary.open('rb') as stream:
                    if stream.read(4) != b'\x7fELF':
                        errors += 1
                        continue
                linked = runner(['ldd', str(binary)])
                if linked.returncode or linked.stderr.strip() or 'not found' in linked.stdout:
                    errors += 1
                libraries += sum(root in line for line in linked.stdout.splitlines())
            except (OSError, subprocess.SubprocessError):
                errors += 1
    script_paths = set(layout.local.glob('*.sh')) | set((layout.local / 'tools/bin').glob('*'))
    script_paths |= set((layout.local / 'scripts').glob('*.sh')) | set(source.glob('*.sh'))
    for script in script_paths:
        try:
            if script.is_file() and not script.is_symlink() and script.stat().st_size <= 1024 * 1024:
                scripts += int(root in script.read_text(errors='replace'))
        except OSError:
            errors += 1
    environment = sum(root in os.environ.get(key, '') for key in ('PATH', 'LD_LIBRARY_PATH'))
    return (f'Positive references: {configs} CMake lines, {libraries} ldd lines, '
            f'{environment} PATH/LD_LIBRARY_PATH values, {scripts} top-level/script-directory scripts; '
            f'{errors} incomplete checks. Search is bounded, not a proof of absence.')


def approve(layout, category, runner=run):
    if category not in ENABLED:
        raise Refusal('category is report-only: no mechanical proof of disuse')
    existing = approvals_read(layout)
    if category in existing:
        print('approval: category already approved (no-op)')
        return
    # Display its first list before recording a category-wide, persistent approval.
    categories, processes = scan(layout, runner)
    display(categories, processes, existing, full=True)
    existing[category] = int(time.time())
    payload = (json.dumps({'version': 1, 'approved': existing}, indent=2) + '\n').encode()
    # Exclusive create: never overwrite another approval or follow a link.
    if layout.approvals.exists():
        raise Refusal('approval file exists; concurrent change refused')
    fd = os.open(layout.approvals, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    print(f'approval: {category} recorded; future matching files are covered')


def scan(layout, runner=run, process_reader=Processes.read):
    processes = process_reader(layout.proc)
    categories = []
    reasons = {
        'cuda-toolkit': 'REPORT ONLY: absence of current build, ldd, environment and script references cannot prove no future use; toolkit deletion disabled',
        'bench-logs': 'REPORT ONLY: a nearby report does not prove every raw result is preserved; no verified lossless summary manifest; reports and summaries retained',
        'firstmate-temp': 'REPORT ONLY: random mktemp suffix has no owner PID; age and closed handles cannot prove the creating job has finished',
        'tmp': 'REPORT ONLY: age, user ownership, empty content and closed handles cannot prove a file is abandoned; it may be a future-use marker',
        'go-build': 'Recognized Go build cache entries only; Go regenerates them from retained source on later builds; excludes all protected files and live users',
    }
    for name in CATEGORIES:
        root = layout.roots[name]
        category = Category(name, reason=reasons[name])
        if name == 'cuda-toolkit' and root.exists():
            category.reason += '. ' + cuda_evidence(layout, runner)
        # The Firstmate exception never authorizes traversing other state.
        if name in {'firstmate-temp', 'tmp'}:
            try:
                entries = list(root.iterdir()) if root.exists() and not root.is_symlink() else []
                for p in entries:
                    if name == 'firstmate-temp' and not re.fullmatch(r'\.claude-autoarm-output\.[A-Za-z0-9]+', p.name):
                        continue
                    info = p.lstat()
                    if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and time.time() - max(info.st_mtime, info.st_ctime) > WEEK:
                        if name != 'tmp' or info.st_size == 0:
                            category.size += info.st_blocks * 512
                            category.count += 1
            except OSError:
                category.skipped['cannot inspect directory completely'] += 1
        else:
            category.size, category.count, errors = measure(root)
            if errors:
                category.skipped['measurement errors (size is a lower bound)'] += errors
        categories.append(category)
        if name != 'go-build' or not root.exists():
            continue
        try:
            no_symlinks(root)
            env = runner(['go', 'env', 'GOCACHE'], env={**os.environ, 'GOTOOLCHAIN': 'local'})
            if env.returncode or env.stderr.strip() or env.stdout.strip() != str(root):
                raise Refusal('Go does not confirm this GOCACHE')
            if processes.uncertain:
                raise Refusal(f'incomplete process visibility ({processes.uncertain} processes)')
            # Only the documented two-level hash layout; no recursive deletion.
            for bucket in sorted(root.iterdir()):
                if not re.fullmatch('[0-9a-f]{2}', bucket.name) or bucket.is_symlink() or not bucket.is_dir():
                    continue
                for p in sorted(bucket.iterdir()):
                    try:
                        info = guard(p, name, layout)
                        processes.unused(p)
                        # Expensive lsof/fuser checks happen just before an actual unlink.
                        category.candidates.append((p, info))
                    except (Refusal, OSError) as exc:
                        category.skipped[str(exc)] += 1
        except (Refusal, OSError, subprocess.SubprocessError) as exc:
            category.skipped[str(exc)] += 1
    return categories, processes


def windows_temp(runner=run):
    command = "$ErrorActionPreference='Stop'; $p=[Environment]::GetEnvironmentVariable('TEMP','Process'); if (-not $p) { throw 'TEMP missing' }; $n=(Get-ChildItem -LiteralPath $p -File -Recurse -Force -ErrorAction Stop | Measure-Object Length -Sum).Sum; [long]$n"
    try:
        result = runner(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], timeout=30)
        if result.returncode or result.stderr.strip():
            return 'unavailable (PowerShell failed); no deletion'
        size = int(result.stdout.strip())
        return f'{human_bytes(size)} logical bytes in %TEMP%; report only'
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'unavailable (no usable PowerShell); no deletion'


def rows(key, fields, values):
    """Emit only the flat table subset of TOON; JSON quoting escapes every cell."""
    print(f'{key}[{len(values)}]{{{",".join(fields)}}}:')
    for value in values:
        print('  ' + ','.join(json.dumps(cell, ensure_ascii=True) for cell in value))


def display(categories, processes, approved, full=False):
    print('bin: ' + json.dumps(str(Path(__file__).absolute()).replace(str(Path.home()), '~', 1)))
    print('description: Report conservative cleanup candidates; permanent deletion requires category approval')
    summary = []
    files = []
    truncated = 0
    for category in categories:
        reason = category.reason
        if category.skipped:
            reason += '; refused: ' + json.dumps(dict(category.skipped), sort_keys=True)
        summary.append((category.name, f'{human_bytes(category.size)} in {category.count} files',
                        f'{human_bytes(category.eligible_bytes)} in {len(category.candidates)} files',
                        'yes' if category.name in approved else 'no', reason))
        limit = len(category.candidates) if full else 5
        for path, info in category.candidates[:limit]:
            files.append((category.name, str(path), info.st_blocks * 512))
        if limit < len(category.candidates):
            truncated += len(category.candidates) - limit
    rows('categories', ('category', 'measured', 'eligible', 'approved', 'reason'), summary)
    rows('files', ('category', 'path', 'allocated_bytes'), files)
    if truncated:
        print(f'list_help: Run python3 clean.py scan --full for the remaining {truncated} files')
    print('ram: report only; possible orphan means adopted/dead parent, no tty, age over 1 hour and lifetime CPU below 0.1%; this is not proof of disuse')
    rows('processes', ('pid', 'name', 'rss', 'possible_orphan'),
         [(p['pid'], p['name'], human_bytes(p['rss']), p['possible_orphan'])
          for p in processes.biggest[:5]])
    print(f'process_visibility: {processes.uncertain} incomplete processes; any incompleteness blocks deletion')


def idle(runner=run):
    result = runner(['jp-gate'])
    if result.returncode or result.stdout.strip() != 'idle' or result.stderr.strip():
        raise Refusal('jp-gate must succeed and print exactly idle')


def record_apply(layout, freed, deleted, refused, failed, runner=run):
    result = runner(['python3', str(layout.local / 'selfimprove/si.py'), 'record',
                     '--task', 'cleaner', '--master', 'codex', '--labour', 'agy,qwen',
                     '--outcome', 'failed' if failed else 'done',
                     '--checks', f'deleted={deleted},refused={refused},freed_bytes={freed}'])
    if result.returncode:
        raise Refusal(f'apply audit record failed; {freed} bytes already unlinked in {deleted} files')


def delete_file(path, previous, layout, runner=run, process_reader=Processes.read):
    # Open each directory relative to its predecessor, preventing symlink substitution.
    # Pin the final parent with a descriptor, then unlink by basename through it.
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parent.parts[1:]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        current = guard(path, 'go-build', layout)
        if fingerprint(current) != fingerprint(previous):
            raise Refusal('file changed since scan')
        process_reader(layout.proc).unused(path)
        check_handles(path, runner)
        idle(runner)
        # Repeat all static checks after external commands.
        latest = guard(path, 'go-build', layout)
        relative = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        if fingerprint(latest) != fingerprint(previous) or fingerprint(relative) != fingerprint(previous):
            raise Refusal('file or pinned parent changed during verification')
        os.unlink(path.name, dir_fd=directory)
        return previous.st_blocks * 512
    finally:
        os.close(directory)


def apply(layout, runner=run, process_reader=Processes.read):
    freed = deleted = refused = 0
    failed = True
    refusals = []
    try:
        idle(runner)
        approved = approvals_read(layout)
        categories, _ = scan(layout, runner, process_reader)
        for category in categories:
            if category.name not in approved or category.name not in ENABLED:
                continue
            refused += sum(category.skipped.values())
            if category.skipped:
                refusals.append(json.dumps(dict(category.skipped), sort_keys=True))
            for path, info in category.candidates:
                try:
                    freed += delete_file(path, info, layout, runner, process_reader)
                    deleted += 1
                except (Refusal, OSError, subprocess.SubprocessError) as exc:
                    refused += 1
                    refusals.append(f'{path}: {exc}')
        failed = refused > 0
        rows('refusals', ('reason',), [(reason,) for reason in refusals])
        print(f'apply: {deleted} files unlinked; {freed} allocated bytes released from names; {refused} refused')
        return 1 if failed else 0
    finally:
        # Also record rejected apply attempts (including a busy/missing gate).
        record_apply(layout, freed, deleted, refused, failed, runner)


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__, epilog='Examples: python3 clean.py; python3 clean.py approve go-build; python3 clean.py apply')
    parser.add_argument('-v', '-V', '--version', action='version', version=VERSION)
    sub = parser.add_subparsers(dest='command')
    scan_parser = sub.add_parser('scan', allow_abbrev=False, help='Report only (default); never writes approval or audit files')
    scan_parser.add_argument('--full', action='store_true', help='List every eligible file (default: first five per category)')
    approve_parser = sub.add_parser('approve', allow_abbrev=False, help='Show list and permanently approve one enabled category')
    approve_parser.add_argument('category', choices=CATEGORIES)
    sub.add_parser('apply', allow_abbrev=False, help='Check idle gate, re-scan approved categories, permanently unlink files and audit')
    args = parser.parse_args(argv)
    layout = Layout(Path.home(), Path('/tmp'), Path(__file__).absolute().parent / 'approved.json')
    try:
        if args.command in {'approve', 'apply'}:
            # Coordinate our own mutating commands without creating a lock file.
            with Path(__file__).open('rb') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if args.command == 'apply':
                        record_apply(layout, 0, 0, 1, True)
                    raise Refusal('another cleaner mutation is running')
                if args.command == 'approve':
                    approve(layout, args.category)
                else:
                    return apply(layout)
        else:
            categories, processes = scan(layout)
            display(categories, processes, approvals_read(layout), getattr(args, 'full', False))
            print('windows_temp: ' + json.dumps(windows_temp()))
            print('help: Review scan --full; approve go-build only if regeneration cost is acceptable; apply permanently deletes approved candidates')
        return 0
    except (Refusal, OSError, ValueError, subprocess.SubprocessError) as exc:
        print('error: ' + json.dumps(str(exc)))
        print('help: Resolve missing safety evidence; run python3 clean.py scan to review again')
        return 1


if __name__ == '__main__':
    sys.exit(main())
