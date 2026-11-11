"""Tests for Qwen tuning settings loading, fallback, and driver integration."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import driver_support
import fill
import qwen_task


class LoadTunablesTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            missing_path = Path(d) / 'missing.json'
            tunables = driver_support.load_tunables(missing_path)
            self.assertEqual(tunables, {'fill_retry_budget': 3, 'split_prompt_tokens': 1500})

    def test_bad_json_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for bad_content in ['{not valid json', '', '   \n  ', '[1, 2, 3]', '"string"', '42', 'true', 'null']:
                bad_path = Path(d) / 'bad.json'
                bad_path.write_text(bad_content, encoding='utf-8')
                tunables = driver_support.load_tunables(bad_path)
                self.assertEqual(tunables, {'fill_retry_budget': 3, 'split_prompt_tokens': 1500})

    def test_out_of_range_values_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            settings_path = Path(d) / 'out_of_range.json'

            # Below min
            settings_path.write_text(json.dumps({'fill_retry_budget': 0, 'split_prompt_tokens': 499}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 3, 'split_prompt_tokens': 1500})

            # Negative values
            settings_path.write_text(json.dumps({'fill_retry_budget': -1, 'split_prompt_tokens': -500}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 3, 'split_prompt_tokens': 1500})

            # Above max
            settings_path.write_text(json.dumps({'fill_retry_budget': 6, 'split_prompt_tokens': 3001}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 3, 'split_prompt_tokens': 1500})

            # Far above max
            settings_path.write_text(json.dumps({'fill_retry_budget': 100, 'split_prompt_tokens': 50000}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 3, 'split_prompt_tokens': 1500})

    def test_wrong_types_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            settings_path = Path(d) / 'wrong_types.json'
            cases = [
                {'fill_retry_budget': '3', 'split_prompt_tokens': '1500'},
                {'fill_retry_budget': 3.0, 'split_prompt_tokens': 1500.0},
                {'fill_retry_budget': True, 'split_prompt_tokens': False},
                {'fill_retry_budget': [3], 'split_prompt_tokens': [1500]},
                {'fill_retry_budget': {'val': 3}, 'split_prompt_tokens': {'val': 1500}},
                {'fill_retry_budget': None, 'split_prompt_tokens': None},
            ]
            for case in cases:
                settings_path.write_text(json.dumps(case))
                self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 3, 'split_prompt_tokens': 1500})

    def test_per_key_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            settings_path = Path(d) / 'per_key.json'

            # Valid budget, invalid split tokens
            settings_path.write_text(json.dumps({'fill_retry_budget': 5, 'split_prompt_tokens': 100}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 5, 'split_prompt_tokens': 1500})

            # Invalid budget, valid split tokens
            settings_path.write_text(json.dumps({'fill_retry_budget': 10, 'split_prompt_tokens': 2000}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 3, 'split_prompt_tokens': 2000})

            # Missing split tokens key
            settings_path.write_text(json.dumps({'fill_retry_budget': 4}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 4, 'split_prompt_tokens': 1500})

            # Missing budget key
            settings_path.write_text(json.dumps({'split_prompt_tokens': 2500}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 3, 'split_prompt_tokens': 2500})

    def test_good_values(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            settings_path = Path(d) / 'good.json'

            # In-range typical values
            settings_path.write_text(json.dumps({'fill_retry_budget': 4, 'split_prompt_tokens': 2000}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 4, 'split_prompt_tokens': 2000})

            # Inclusive lower bounds
            settings_path.write_text(json.dumps({'fill_retry_budget': 1, 'split_prompt_tokens': 500}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 1, 'split_prompt_tokens': 500})

            # Inclusive upper bounds
            settings_path.write_text(json.dumps({'fill_retry_budget': 5, 'split_prompt_tokens': 3000}))
            self.assertEqual(driver_support.load_tunables(settings_path), {'fill_retry_budget': 5, 'split_prompt_tokens': 3000})

    def test_never_raise(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            dir_path = Path(d) / 'is_a_dir'
            dir_path.mkdir()
            # Directory path instead of file
            self.assertEqual(driver_support.load_tunables(dir_path), {'fill_retry_budget': 3, 'split_prompt_tokens': 1500})

    def test_default_settings_path_override(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            custom_path = Path(d) / 'custom_qwen.json'
            custom_path.write_text(json.dumps({'fill_retry_budget': 4, 'split_prompt_tokens': 2400}))
            with patch.object(driver_support, 'DEFAULT_SETTINGS_PATH', custom_path):
                self.assertEqual(driver_support.load_tunables(), {'fill_retry_budget': 4, 'split_prompt_tokens': 2400})


class FillDefaultTests(unittest.TestCase):
    def _run_fill(self, tries_flag: int | None, tunables: dict[str, int]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as d:
            temp_dir = Path(d)
            settings_path = temp_dir / 'qwen.json'
            settings_path.write_text(json.dumps(tunables))

            repo = temp_dir / 'repo'
            repo.mkdir()
            (repo / '.git').mkdir()
            source = repo / 'calc.py'
            source.write_text('def f(x: int) -> int:\n    """Identity."""\n    raise NotImplementedError\n')
            test = repo / 'test_calc.py'
            test.write_text('# test\n')

            results_dir = temp_dir / 'results'
            argv = ['fill', str(source), 'f', str(test), 'test_calc', '--results', str(results_dir), '--no-selfimprove', '--no-typecheck']
            if tries_flag is not None:
                argv.extend(['--tries', str(tries_flag)])

            reply = 'def f(x: int) -> int:\n    return x\nREASON: identity'

            def fake_ask(prompt: str, seed: int) -> str:
                return reply

            def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(['test'], 0, 'ok', '')

            with patch.object(sys, 'argv', argv), \
                 patch('fill.ask', side_effect=fake_ask), \
                 patch('fill.tokenize_count', return_value=123), \
                 patch('fill.subprocess.run', side_effect=fake_run), \
                 patch('fill.load_tunables', side_effect=lambda: driver_support.load_tunables(settings_path)), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                code = fill.main()

            self.assertEqual(code, 0)
            verdict_path = next(results_dir.glob('*/verdict.json'))
            metadata: dict[str, object] = json.loads(verdict_path.read_text())
            return metadata

    def test_fill_default_from_tunables(self) -> None:
        meta = self._run_fill(tries_flag=None, tunables={'fill_retry_budget': 4, 'split_prompt_tokens': 1500})
        self.assertEqual(meta['attempt_budget'], 4)

    def test_fill_explicit_flag_wins_over_tunables_default(self) -> None:
        meta = self._run_fill(tries_flag=2, tunables={'fill_retry_budget': 5, 'split_prompt_tokens': 1500})
        self.assertEqual(meta['attempt_budget'], 2)


class SplitAdvisedTests(unittest.TestCase):
    @unittest.skipUnless(Path('/home/chopp/local-llm/driver/selftest/pass-spec.json').exists(), 'fixed fixture unavailable')
    def _run_task_with_spec(self, token_estimate: int, tunables: dict[str, int]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as d:
            temp_dir = Path(d)
            settings_path = temp_dir / 'qwen.json'
            settings_path.write_text(json.dumps(tunables))

            fixture_spec = Path('/home/chopp/local-llm/driver/selftest/pass-spec.json')
            spec_data: dict[str, object] = json.loads(fixture_spec.read_text())
            spec_data['token_estimate'] = token_estimate
            spec_data['transport'] = 'diff'
            spec_file = temp_dir / 'spec.json'
            spec_file.write_text(json.dumps(spec_data))

            results_dir = temp_dir / 'results'
            argv = ['qwen-task', str(spec_file), '--results', str(results_dir), '--no-typecheck']

            reply = (
                'diff --git a/calc.py b/calc.py\n'
                '--- a/calc.py\n'
                '+++ b/calc.py\n'
                '@@ -1,2 +1,2 @@\n'
                '-def add_one(value):\n'
                '-    return value\n'
                '+def add_one(value):\n'
                '+    return value + 1\n'
                'REASON: example\n'
            )

            real_run = subprocess.run

            def fake_run(*args, **kwargs):
                if args and args[0] == ['qwen-server', 'status']:
                    return subprocess.CompletedProcess(args[0], 0, 'Qwen server running', '')
                return real_run(*args, **kwargs)

            def fake_model(prompt, seed=None):
                return reply, 0.01

            with patch.object(sys, 'argv', argv), \
                 patch('qwen_task.request_model', side_effect=fake_model), \
                 patch('qwen_task.tokenize_count', return_value=100), \
                 patch('qwen_task.subprocess.run', side_effect=fake_run), \
                 patch('qwen_task.load_tunables', side_effect=lambda: driver_support.load_tunables(settings_path)), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = qwen_task.main()

            self.assertEqual(code, 0)
            verdict_path = next(results_dir.glob('*/verdict.json'))
            metadata: dict[str, object] = json.loads(verdict_path.read_text())
            return metadata

    def test_split_advised_when_estimate_exceeds_threshold(self) -> None:
        # 2000 exceeds threshold 1500 -> split_advised: true
        meta = self._run_task_with_spec(token_estimate=2000, tunables={'fill_retry_budget': 3, 'split_prompt_tokens': 1500})
        self.assertEqual(meta['verdict'], 'pass')
        self.assertIs(meta.get('split_advised'), True)

    def test_split_not_advised_when_estimate_equal_threshold(self) -> None:
        # 1500 does not exceed threshold 1500 -> no split_advised
        meta = self._run_task_with_spec(token_estimate=1500, tunables={'fill_retry_budget': 3, 'split_prompt_tokens': 1500})
        self.assertEqual(meta['verdict'], 'pass')
        self.assertNotIn('split_advised', meta)

    def test_split_not_advised_when_estimate_below_threshold(self) -> None:
        # 1000 is below threshold 1500 -> no split_advised
        meta = self._run_task_with_spec(token_estimate=1000, tunables={'fill_retry_budget': 3, 'split_prompt_tokens': 1500})
        self.assertEqual(meta['verdict'], 'pass')
        self.assertNotIn('split_advised', meta)

    def test_split_advised_with_custom_tunables_threshold(self) -> None:
        # With threshold 2500:
        # 2000 does not exceed 2500 -> no split_advised
        meta_under = self._run_task_with_spec(token_estimate=2000, tunables={'fill_retry_budget': 3, 'split_prompt_tokens': 2500})
        self.assertNotIn('split_advised', meta_under)

        # 2600 exceeds 2500 -> split_advised: true
        meta_over = self._run_task_with_spec(token_estimate=2600, tunables={'fill_retry_budget': 3, 'split_prompt_tokens': 2500})
        self.assertIs(meta_over.get('split_advised'), True)


if __name__ == '__main__':
    unittest.main()
