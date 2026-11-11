#!/usr/bin/env python3
"""Run paired real-model measurements; fixed external fixtures are read-only."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
TASKS = {
    'concat': ('def concat_strings(s1: str, s2: str) -> str:\n    """Concatenate s1 and s2 without a separator."""\n    raise NotImplementedError\n', 'concat_strings', 'self.assertIsInstance(concat_strings("foo", "bar"), str)'),
    'maximum': ('def find_max(nums: list[int]) -> int:\n    """Return the largest integer; raise ValueError on empty input."""\n    raise NotImplementedError\n', 'find_max', 'self.assertEqual(find_max([1, 3, 2]), 3)'),
    'first': ('def first_or_zero(values: list[int]) -> int:\n    """Return the first item, or zero for empty input."""\n    raise NotImplementedError\n', 'first_or_zero', 'self.assertEqual(first_or_zero([7, 8]), 7)'),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--results', type=Path, default=ROOT / 'results/ab')
    ap.add_argument('--seeds', type=int, nargs='+', default=[0, 1])
    ns = ap.parse_args()
    ns.results = ns.results.resolve(); ns.results.mkdir(parents=True, exist_ok=True)
    records = []
    subprocess.run(['qwen-server', 'start'], check=True)
    try:
        for seed in ns.seeds:
            for task in ['pass', 'fail', *TASKS]:
                # Alternate which treatment gets the warm model slot first.
                for typed in ([False, True] if seed % 2 == 0 else [True, False]):
                    mode = 'typed' if typed else 'untyped'
                    output = ns.results / f'{task}-{seed}-{mode}'; output.mkdir()
                    if task in ('pass', 'fail'):
                        spec = Path('/home/chopp/local-llm/driver/selftest') / f'{task}-spec.json'
                        cmd = [sys.executable, str(ROOT / 'qwen_task.py'), str(spec)]
                    else:
                        repo = output / 'fixture'; repo.mkdir()
                        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
                        source, func, assertion = TASKS[task]
                        (repo / 'calc.py').write_text(source)
                        test = repo / 'test_calc.py'
                        test.write_text(f'import unittest\nfrom calc import {func}\n\nclass Tests(unittest.TestCase):\n    def test_example(self):\n        {assertion}\n')
                        cmd = [sys.executable, str(ROOT / 'fill.py'), str(repo / 'calc.py'), func, str(test), 'test_calc', '--no-selfimprove']
                    cmd += ['--seed', str(seed), '--results', str(output / 'jobs')]
                    cmd.append('--typecheck' if typed else '--no-typecheck')
                    start = time.monotonic()
                    completed = subprocess.run(cmd, text=True, capture_output=True)
                    wall = time.monotonic() - start
                    (output / 'stdout.txt').write_text(completed.stdout)
                    (output / 'stderr.txt').write_text(completed.stderr)
                    verdict_path = next((output / 'jobs').glob('*/verdict.json'))
                    verdict = json.loads(verdict_path.read_text())
                    jobs = verdict.get('jobs', [])
                    record = {'task': task, 'seed': seed, 'typed': typed,
                              'passed': verdict['verdict'] == 'pass', 'attempts': len(jobs),
                              'wall_seconds': round(wall, 3),
                              'checker_catches': sum(j.get('checker_caught_test_miss', False) for j in jobs),
                              'prompt_tokens': sum(j['prompt_tokens'] for j in jobs),
                              'output_lines': sum(j['output_lines'] for j in jobs),
                              'verdict': str(verdict_path.relative_to(ROOT))}
                    records.append(record)
                    (ns.results / 'summary.json').write_text(json.dumps(records, indent=2) + '\n')
                    print(json.dumps(record), flush=True)
    finally:
        subprocess.run(['qwen-server', 'stop'], check=True)


if __name__ == '__main__':
    main()
