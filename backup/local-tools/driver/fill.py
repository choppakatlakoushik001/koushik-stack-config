#!/usr/bin/env python3
"""Ask local Qwen to write one Python function body, splice it in, run tests.

Usage: fill.py FILE FUNC TEST_FILE TEST_MODULE [--tries N] [--note TEXT]

Qwen sees the function's signature and docstring plus the test file and returns
the whole function. Each try is spliced into a scratch copy of the repository
and TEST_MODULE runs there. Opt in to strict static checking with --typecheck;
a function passing all enabled checks is written into FILE.
Nothing is written when every try fails. Small models break diff syntax, so this
asks for plain code instead.
"""
import argparse
import time
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from driver_support import TYPE_INSTRUCTION, checker_command, typecheck, result_directory, write_metadata, load_tunables
from explanations import reason_line
from qwen_task import tokenize_count
from pathlib import Path

SERVER = 'http://127.0.0.1:8080/v1/chat/completions'


def func_span(src, name):
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node.lineno, node.end_lineno
    raise SystemExit(f'function {name} not found')


def ask(prompt, seed):
    body = json.dumps({'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.2 if seed else 0,
                       'seed': seed, 'max_tokens': 900}).encode()
    req = urllib.request.Request(SERVER, body, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())['choices'][0]['message']['content']


def extract(reply, name):
    for block in re.findall(r'```(?:python)?\n(.*?)(?:```|\Z)', reply, re.S) or [reply]:
        block = '\n'.join(l for l in block.splitlines() if not l.lstrip('# ').upper().startswith('REASON:'))
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return '\n'.join(block.splitlines()[node.lineno - 1:node.end_lineno])
    return None


def main():
    tunables = load_tunables()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('file'); ap.add_argument('func'); ap.add_argument('test_file'); ap.add_argument('test_module')
    ap.add_argument('--tries', type=int, default=tunables['fill_retry_budget']); ap.add_argument('--note', default='')
    type_flags = ap.add_mutually_exclusive_group()
    type_flags.add_argument('--typecheck', action='store_true', help='Enable annotation prompting and strict static checking (default: off)')
    type_flags.add_argument('--no-typecheck', dest='typecheck', action='store_false', help='Keep checking disabled (default; compatibility flag)')
    ap.set_defaults(typecheck=False)
    ap.add_argument('--no-selfimprove', action='store_true', help='Skip the legacy global summary event (isolated measurements)')
    ap.add_argument('--seed', type=int, default=0, help='First attempt seed; later attempts increment it')
    ap.add_argument('--results', type=Path, default=Path.home() / 'local-llm/driver/results')
    a = ap.parse_args()
    if a.tries < 1:
        ap.error('--tries must be positive')
    result = result_directory(a.results)
    start_t = time.monotonic()
    jobs = []
    metadata = {'verdict': 'fail', 'jobs': jobs, 'typecheck_enabled': a.typecheck,
                'result_dir': str(result), 'attempt_budget': a.tries}
    try:
        checker = checker_command() if a.typecheck else None
        metadata['checker'] = checker
        path = Path(a.file).resolve(); repo = path.parent
        while not (repo / '.git').exists() and repo != repo.parent:
            repo = repo.parent
        if repo == repo.parent:
            raise ValueError('file must be inside a Git repository')
        metadata['repo'] = str(repo)
        src = path.read_text(); lines = src.splitlines()
        start, end = func_span(src, a.func)
        header = '\n'.join(lines[:start - 1])
        stub = '\n'.join(lines[start - 1:end])
        prompt = (f'Write the complete Python function `{a.func}`. Keep its calling convention and docstring and '
                  f'replace `raise NotImplementedError` with a working implementation. Use only names that '
                  f'already exist in the module. Return one ```python block with only that function, then '
                  f'one line starting with REASON: explaining from first principles why it works.\n'
                  f'{TYPE_INSTRUCTION if checker else ""}{a.note}\n\nModule header:\n```python\n{header[-2500:]}\n```\n\nFunction:\n```python\n{stub}\n```\n\n'
                  f'Tests it must pass:\n```python\n{Path(a.test_file).read_text()[:4000]}\n```')
        feedback = ''
        for t in range(a.tries):
            attempt = result / f'attempt-{t + 1}'; attempt.mkdir()
            actual_prompt = prompt + feedback
            (attempt / 'prompt.txt').write_text(actual_prompt)
            tokens = tokenize_count(actual_prompt)
            job = {'attempt': t + 1, 'seed': a.seed + t, 'prompt_tokens': tokens,
                   'output_lines': 0, 'verdict': 'fail', 'test_exit_code': None,
                   'typecheck_exit_code': None}
            jobs.append(job)
            write_metadata(result, metadata)
            model_start = time.monotonic()
            reply = ask(actual_prompt, a.seed + t)
            job['model_seconds'] = round(time.monotonic() - model_start, 3)
            job['output_lines'] = len(reply.splitlines())
            completion_toks = 0
            try:
                completion_toks = tokenize_count(reply)
            except Exception:
                pass
            job['completion_tokens'] = completion_toks
            (attempt / 'model_reply.txt').write_text(reply)
            func = extract(reply, a.func)
            job['reason'] = reason_line(reply)
            print(f'try {t + 1}: reason: {job["reason"] or "(none given)"}', file=sys.stderr)
            if not func:
                job['reason'] = 'no parsable function'
                job['failure_kind'] = 'malformed_patch'
                feedback = f'\n\nYour previous reply had no parsable function:\n{reply[-1500:]}\nReturn the complete function in a Python code block.'
                write_metadata(result, metadata)
                continue
            job['code_lines'] = len(func.splitlines())
            new = '\n'.join(lines[:start - 1] + func.splitlines() + lines[end:]) + '\n'
            with tempfile.TemporaryDirectory() as d:
                copy = Path(d) / 'repo'
                def ignore(directory, names):
                    return [name for name in names if name in ('.git', '__pycache__')
                            or (Path(directory) / name).resolve() == a.results.expanduser().resolve()]
                shutil.copytree(repo, copy, ignore=ignore)
                (copy / path.relative_to(repo)).write_text(new)
                try:
                    r = subprocess.run([sys.executable, '-m', 'unittest', a.test_module], cwd=copy,
                                       capture_output=True, text=True, timeout=120,
                                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
                    test_code, test_output = r.returncode, r.stdout + r.stderr
                except subprocess.TimeoutExpired:
                    test_code, test_output = 124, 'Tests timed out after 120 seconds.'
                check_code, check_output = typecheck(copy, [str(path.relative_to(repo))], checker) if checker else (None, 'Disabled.\n')
            (attempt / 'test_output.txt').write_text(test_output)
            (attempt / 'typecheck_output.txt').write_text(check_output)
            passed = test_code == 0 and check_code in (None, 0)
            if passed:
                kind = 'none'
            elif test_code != 0:
                kind = 'timeout' if test_code == 124 else 'tests'
            elif check_code not in (None, 0):
                kind = 'timeout' if check_code == 124 else 'typecheck'
            else:
                kind = 'tests'
            job.update(test_exit_code=test_code, typecheck_exit_code=check_code,
                       checker_caught_test_miss=test_code == 0 and check_code not in (None, 0),
                       verdict='pass' if passed else 'fail', failure_kind=kind)
            write_metadata(result, metadata)
            if passed:
                path.write_text(new)
                metadata['verdict'] = 'pass'
                metadata['failure_kind'] = 'none'
                return 0
            feedback = (f'\n\nYour previous attempt:\n```python\n{func}\n```\n'
                        f'Tests (exit {test_code}):\n{test_output[-1500:]}\n'
                        f'Static type checker (exit {check_code}):\n{check_output[-1500:]}\nFix it.')
        return 1
    except Exception as exc:
        metadata['reason'] = str(exc)
        return 1
    finally:
        metadata['attempts'] = len(jobs)
        metadata['wall_seconds'] = round(time.monotonic() - start_t, 3)
        if 'failure_kind' not in metadata:
            metadata['failure_kind'] = jobs[-1]['failure_kind'] if jobs and 'failure_kind' in jobs[-1] else ('none' if metadata.get('verdict') == 'pass' else 'refused_spec')
        write_metadata(result, metadata)
        if not a.no_selfimprove:
            log(a.func, len(jobs), 'done' if metadata['verdict'] == 'pass' else 'failed', start_t)
        print(json.dumps(metadata, indent=2))


def log(func, tries, outcome, start_t):
    # Preserve the existing weekly summary event alongside detailed result files.
    subprocess.run([sys.executable, '/home/chopp/local-llm/selfimprove/si.py', 'record', '--task', f'fill:{func}',
                    '--master', 'claude', '--labour', 'qwen', '--outcome', outcome,
                    '--minutes', f'{(time.monotonic() - start_t) / 60:.1f}', '--checks', f'tries={tries}'],
                   capture_output=True, check=False)


if __name__ == '__main__':
    sys.exit(main())
