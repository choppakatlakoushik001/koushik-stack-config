"""Exercise real validation and retry decisions without a model server."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import driver_support
import fill
import qwen_task


class CheckerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pyright') or shutil.which('mypy'), 'local checker required')
    def test_checker_catches_branch_test_misses_and_ignores_repo_config(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / 'repo'; work.mkdir()
            (work / 'calc.py').write_text('def f(x: bool) -> int:\n    if x:\n        return 1\n')
            (work / 'pyrightconfig.json').write_text('{"typeCheckingMode":"off"}')
            code, output = driver_support.typecheck(work, ['calc.py'], driver_support.checker_command())
            self.assertNotEqual(code, 0, output)
            self.assertIn('return', output.lower())
            (work / 'calc.py').write_text('def f(x: bool) -> int:\n    return 1 if x else 0\n')
            self.assertEqual(driver_support.typecheck(work, ['calc.py'], driver_support.checker_command())[0], 0)

    def test_missing_checker_has_actionable_error(self):
        with patch('driver_support.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '--no-typecheck'):
                driver_support.checker_command()

    def test_non_python_does_not_invoke_checker(self):
        self.assertEqual(driver_support.typecheck(Path('/unused'), ['a.txt'], ['missing'])[0], 0)


class FillLoopTests(unittest.TestCase):
    def run_fill(self, *, enabled=True, replies=None, checks=None, tries=3, test_codes=None, flag=None):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / 'repo'; repo.mkdir(); (repo / '.git').mkdir()
            source = repo / 'calc.py'
            original = 'def f(x: int) -> int:\n    """Identity."""\n    raise NotImplementedError\n'
            source.write_text(original)
            test = repo / 'test_calc.py'; test.write_text('# public tests\n')
            argv = ['fill', str(source), 'f', str(test), 'test_calc', '--results', str(Path(d) / 'results'), '--tries', str(tries), '--no-selfimprove']
            if flag: argv.append(flag)
            elif enabled: argv.append('--typecheck')
            prompts = []
            reply_iter = iter(replies or ['def f(x: int) -> int:\n    return x\nREASON: identity'])
            def ask(prompt, seed):
                prompts.append((prompt, seed)); return next(reply_iter)
            test_iter = iter(test_codes or [0] * tries)
            def run(*args, **kwargs):
                return subprocess.CompletedProcess(args, next(test_iter), 'test output', 'test error')
            with patch.object(sys, 'argv', argv), patch('fill.ask', side_effect=ask), patch('fill.tokenize_count', return_value=123), patch('fill.checker_command', return_value=['pyright']) as discovery, patch('fill.typecheck', side_effect=checks or [(0, 'ok')]) as checker, patch('fill.subprocess.run', side_effect=run), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = fill.main()
            result = next((Path(d) / 'results').iterdir())
            metadata = json.loads((result / 'verdict.json').read_text())
            return code, metadata, prompts, source.read_text(), original, checker.call_count, discovery.call_count

    def test_checker_failure_retries_even_when_tests_pass(self):
        reply = 'def f(x: int) -> int:\n    return x\nREASON: identity'
        code, meta, prompts, source, _, _, _ = self.run_fill(replies=[reply, reply], checks=[(1, 'wrong return type'), (0, 'ok')])
        self.assertEqual(code, 0)
        self.assertEqual(meta['attempts'], 2)
        self.assertTrue(meta['jobs'][0]['checker_caught_test_miss'])
        self.assertIn('wrong return type', prompts[1][0])
        self.assertEqual([seed for _, seed in prompts], [0, 1])
        self.assertEqual(meta['jobs'][0]['prompt_tokens'], 123)
        self.assertEqual(meta['jobs'][0]['completion_tokens'], 123)
        self.assertEqual(meta['jobs'][0]['output_lines'], 3)
        self.assertNotIn('NotImplementedError', source)

    def test_default_disables_prompt_and_checker_but_logs_size(self):
        code, meta, prompts, _, _, calls, discoveries = self.run_fill(enabled=False)
        self.assertEqual(code, 0)
        self.assertNotIn(driver_support.TYPE_INSTRUCTION, prompts[0][0])
        self.assertEqual((calls, discoveries), (0, 0))
        self.assertIsNone(meta['jobs'][0]['typecheck_exit_code'])
        self.assertFalse(meta['typecheck_enabled'])
        self.assertEqual(meta['jobs'][0]['prompt_tokens'], 123)
        self.assertEqual(meta['jobs'][0]['completion_tokens'], 123)
        self.assertEqual(meta['jobs'][0]['output_lines'], 3)
        self.assertEqual(meta['jobs'][0]['verdict'], 'pass')

    def test_no_typecheck_compatibility_flag(self):
        code, meta, _, _, _, calls, discoveries = self.run_fill(flag='--no-typecheck')
        self.assertEqual(code, 0)
        self.assertFalse(meta['typecheck_enabled'])
        self.assertEqual((calls, discoveries), (0, 0))

    def test_budget_exhaustion_leaves_source_unchanged(self):
        reply = 'def f(x: int) -> int:\n    return x'
        code, meta, _, source, original, _, _ = self.run_fill(replies=[reply] * 3, checks=[(1, 'bad')] * 3)
        self.assertEqual(code, 1)
        self.assertEqual(meta['attempts'], 3)
        self.assertEqual(source, original)

    def test_tests_still_fail_when_checker_passes(self):
        code, meta, _, source, original, _, _ = self.run_fill(tries=1, test_codes=[1])
        self.assertEqual(code, 1)
        self.assertEqual(meta['jobs'][0]['typecheck_exit_code'], 0)
        self.assertEqual(source, original)

    def test_parse_failure_is_recorded_and_fed_back(self):
        code, meta, prompts, *_ = self.run_fill(replies=['bad syntax', 'def f(x: int) -> int:\n    return x'])
        self.assertEqual(code, 0)
        self.assertEqual(meta['jobs'][0]['verdict'], 'fail')
        self.assertIn('no parsable function', prompts[1][0])


class TaskDriverTests(unittest.TestCase):
    @unittest.skipUnless(Path('/home/chopp/local-llm/driver/selftest/pass-spec.json').exists() and (shutil.which('pyright') or shutil.which('mypy')), 'fixed fixture or local checker unavailable')
    def run_task(self, reply, enabled=True, flag=None):
        spec = Path('/home/chopp/local-llm/driver/selftest/pass-spec.json')
        real_run = subprocess.run
        prompts = []
        def run(args, **kwargs):
            if args == ['qwen-server', 'status']:
                return subprocess.CompletedProcess(args, 0, 'Qwen server running', '')
            return real_run(args, **kwargs)
        def model(prompt, seed):
            prompts.append(prompt)
            return reply, 0.01
        with tempfile.TemporaryDirectory() as d:
            spec_copy = Path(d) / 'spec.json'
            spec_data = json.loads(spec.read_text())
            spec_data['transport'] = 'diff'
            spec_copy.write_text(json.dumps(spec_data))
            argv = ['qwen-task', str(spec_copy), '--results', d, '--seed', '0']
            if flag: argv.append(flag)
            elif enabled: argv.append('--typecheck')
            with patch.object(sys, 'argv', argv), patch('qwen_task.request_model', side_effect=model), patch('qwen_task.tokenize_count', return_value=100), patch('qwen_task.subprocess.run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                code = qwen_task.main()
            metadata = json.loads(next(Path(d).glob('*/verdict.json')).read_text())
            return code, metadata, prompts

    def test_single_attempt_type_error_fails_when_named_tests_pass(self):
        # A literal float equals the expected integer at runtime but violates -> int.
        reply = 'diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n-def add_one(value):\n-    return value\n+def add_one(value: int) -> int:\n+    return 5.0\nREASON: example only\n'
        code, meta, prompts = self.run_task(reply)
        self.assertEqual(code, 1)
        self.assertEqual(meta['attempts'], 1)
        self.assertEqual(meta['test_exit_code'], 0)
        self.assertNotEqual(meta['typecheck_exit_code'], 0)
        self.assertTrue(meta['jobs'][0]['checker_caught_test_miss'])
        self.assertIn(driver_support.TYPE_INSTRUCTION, prompts[0])
        self.assertEqual(meta['jobs'][0]['output_lines'], len(reply.splitlines()))

    def test_default_accepts_same_patch_without_typecheck_and_logs_size(self):
        reply = 'diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n-def add_one(value):\n-    return value\n+def add_one(value: int) -> int:\n+    return 5.0\nREASON: example only\n'
        code, meta, prompts = self.run_task(reply, enabled=False)
        self.assertEqual(code, 0)
        self.assertIsNone(meta['jobs'][0]['typecheck_exit_code'])
        self.assertNotIn(driver_support.TYPE_INSTRUCTION, prompts[0])
        self.assertFalse(meta['typecheck_enabled'])
        self.assertEqual(meta['jobs'][0]['prompt_tokens'], 100)
        self.assertEqual(meta['jobs'][0]['completion_tokens'], 100)
        self.assertGreater(meta['jobs'][0]['output_lines'], 0)
        self.assertEqual(meta['jobs'][0]['verdict'], 'pass')

    def test_no_typecheck_compatibility_flag(self):
        reply = 'diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n-def add_one(value):\n-    return value\n+def add_one(value: int) -> int:\n+    return 5.0\nREASON: example only\n'
        code, meta, _ = self.run_task(reply, flag='--no-typecheck')
        self.assertEqual(code, 0)
        self.assertFalse(meta['typecheck_enabled'])

    def test_malformed_generation_keeps_job_measurements(self):
        code, meta, _ = self.run_task('broken diff\nREASON: malformed')
        self.assertEqual(code, 1)
        self.assertEqual(meta['jobs'][0]['output_lines'], 2)
        self.assertEqual(meta['jobs'][0]['prompt_tokens'], 100)
        self.assertEqual(meta['jobs'][0]['verdict'], 'fail')


if __name__ == '__main__':
    unittest.main()
