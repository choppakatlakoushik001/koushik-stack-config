"""Shared local validation and durable per-generation measurements."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

_DEFAULT_PATH: Path = Path.home() / 'local-llm/selfimprove/settings/qwen.json'
DEFAULT_SETTINGS_PATH: Path = _DEFAULT_PATH
DEFAULT_TUNABLES: dict[str, int] = {
    'fill_retry_budget': 3,
    'split_prompt_tokens': 1500,
}


def load_tunables(path: Path | str = DEFAULT_SETTINGS_PATH) -> dict[str, int]:
    """Load Qwen tuning settings with per-key fallback to defaults. Never raise."""
    tunables: dict[str, int] = dict(DEFAULT_TUNABLES)
    try:
        target = DEFAULT_SETTINGS_PATH if (path is _DEFAULT_PATH or path is None) else path
        resolved = Path(target).expanduser()
        data: object = json.loads(resolved.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            retry = data.get('fill_retry_budget')
            if type(retry) is int and 1 <= retry <= 5:
                tunables['fill_retry_budget'] = retry

            split = data.get('split_prompt_tokens')
            if type(split) is int and 500 <= split <= 3000:
                tunables['split_prompt_tokens'] = split
    except Exception:
        pass
    return tunables


TYPE_INSTRUCTION = (
    'Use explicit parameter and return type annotations on every function you change. '
    'Preserve existing annotated contracts. The changed Python files must pass strict '
    'static type checking; avoid Any, type: ignore, and checker suppression directives.\n'
)


VALID_FAILURE_KINDS: set[str] = {
    'none',
    'refused_spec',
    'malformed_patch',
    'patch_apply',
    'typecheck',
    'tests',
    'timeout',
    'server',
}

_DIAG_RE = re.compile(r'^\s*(.*?):(\d+)(?::\d+)?(?::|\s*-)\s*(?:error|Error):\s*(.+)$')
_HUNK_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')


def get_failure_kind(verdict: dict[str, object]) -> str:
    """Return failure_kind from verdict, deriving it for older verdicts if missing."""
    kind = verdict.get('failure_kind')
    if isinstance(kind, str) and kind in VALID_FAILURE_KINDS:
        return kind
    if verdict.get('verdict') == 'pass':
        return 'none'
    test_code = verdict.get('test_exit_code')
    if test_code is not None and test_code != 0:
        return 'timeout' if test_code == 124 else 'tests'
    typecheck_code = verdict.get('typecheck_exit_code')
    if typecheck_code not in (None, 0):
        return 'timeout' if typecheck_code == 124 else 'typecheck'
    reason = str(verdict.get('reason', '')).lower()
    if any(k in reason for k in ('malformed', 'hunk', 'empty patch', 'diff --git', 'fences')):
        return 'malformed_patch'
    if 'apply' in reason:
        return 'patch_apply'
    if 'server' in reason:
        return 'server'
    if 'timeout' in reason or 'timed out' in reason:
        return 'timeout'
    return 'refused_spec'


def parse_diagnostics(output: str, work: Path) -> list[dict[str, object]]:
    """Parse static type checker error lines into structured diagnostic records."""
    diags: list[dict[str, object]] = []
    work_resolved = work.resolve()
    for line in output.splitlines():
        m = _DIAG_RE.match(line)
        if m:
            file_part, line_str, msg = m.groups()
            p = Path(file_part.strip())
            try:
                rel = p.resolve().relative_to(work_resolved).as_posix()
            except ValueError:
                rel = p.name
            diags.append({
                'file': rel,
                'line': int(line_str),
                'message': msg.strip(),
            })
    return diags


def changed_line_ranges_from_patch(patch: str) -> dict[str, set[int]]:
    """Extract line numbers in the patched files that were modified or added."""
    ranges: dict[str, set[int]] = {}
    current_file: str | None = None
    for line in patch.splitlines():
        if line.startswith('+++ '):
            name = line[4:].split('\t', 1)[0]
            if name.startswith(('a/', 'b/')):
                name = name[2:]
            current_file = name
            if current_file not in ranges:
                ranges[current_file] = set()
        elif line.startswith('@@ ') and current_file is not None:
            m = _HUNK_RE.match(line)
            if m:
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) is not None else 1
                for ln in range(new_start, new_start + max(1, new_count)):
                    ranges[current_file].add(ln)
    return ranges


def checker_command() -> list[str]:
    for name in ('pyright', 'mypy'):
        executable = shutil.which(name)
        if executable:
            return [executable]
    raise RuntimeError('No local pyright or mypy found; install a checker in the driver tools environment or use --no-typecheck.')


def typecheck(
    work: Path,
    paths: list[str],
    command: list[str],
    *,
    scope: str = 'file',
    base_output: str | None = None,
    patch: str | None = None,
) -> tuple[int, str]:
    """Check entire changed Python files using driver-owned strict configuration."""
    files = [str(work / p) for p in paths if p.endswith('.py')]
    if not files:
        return 0, 'No changed Python files.\n'
    with tempfile.TemporaryDirectory(prefix='qwen-check-', dir=work.parent) as scratch:
        if Path(command[0]).name == 'pyright':
            config = Path(scratch) / 'pyrightconfig.json'
            config.write_text(json.dumps({
                'typeCheckingMode': 'strict',
                'extraPaths': [str(work)],
            }))
            args = command + ['--project', str(config), '--pythonpath', sys.executable, *files]
        else:
            args = command + ['--strict', '--config-file', '/dev/null', '--no-incremental',
                              '--follow-imports=silent', *files]
        try:
            result = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=120)
            code, output = result.returncode, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return 124, 'Static type checker timed out after 120 seconds.\n'
        except OSError as exc:
            return 127, f'Static type checker failed to start: {exc}\n'

    if scope == 'changed' and patch is not None:
        if code == 0:
            return 0, output
        base_diags = parse_diagnostics(base_output or '', work)
        base_errors = {(d['file'], d['message']) for d in base_diags}
        patched_diags = parse_diagnostics(output, work)
        changed_ranges = changed_line_ranges_from_patch(patch)

        new_errors = [
            d for d in patched_diags
            if int(d['line']) in changed_ranges.get(str(d['file']), set())
            and (d['file'], d['message']) not in base_errors
        ]
        if not new_errors:
            return 0, (f'Scoped typecheck passed: {len(patched_diags)} errors ignored '
                       f'as pre-existing or outside changed lines.\n{output}')
        return code, f'Scoped typecheck failed: {len(new_errors)} new errors in changed lines.\n{output}'

    return code, output


def result_directory(root: Path) -> Path:
    result = root.expanduser().resolve() / (dt.datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
    result.mkdir(parents=True)
    return result


def write_metadata(result: Path, metadata: dict[str, object]) -> None:
    (result / 'verdict.json').write_text(json.dumps(metadata, indent=2) + '\n')
