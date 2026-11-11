import contextlib
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import si


class TestDriverJobs(unittest.TestCase):
    def test_in_bucket(self):
        # PROMPT_TOKEN_BUCKETS = [(0, 500, '<500'), (500, 1000, '500-999'), (1000, 2000, '1000-1999'), (2000, None, '2000+')]
        self.assertTrue(si.in_bucket(0, 0, 500))
        self.assertTrue(si.in_bucket(499, 0, 500))
        self.assertFalse(si.in_bucket(500, 0, 500))
        self.assertTrue(si.in_bucket(500, 500, 1000))
        self.assertTrue(si.in_bucket(999, 500, 1000))
        self.assertFalse(si.in_bucket(1000, 500, 1000))
        self.assertTrue(si.in_bucket(1000, 1000, 2000))
        self.assertTrue(si.in_bucket(1999, 1000, 2000))
        self.assertFalse(si.in_bucket(2000, 1000, 2000))
        self.assertTrue(si.in_bucket(2000, 2000, None))
        self.assertTrue(si.in_bucket(10000, 2000, None))

    def test_in_bucket_missing_or_invalid_values(self):
        self.assertFalse(si.in_bucket(None, 0, 500))
        self.assertFalse(si.in_bucket('invalid', 0, 500))
        self.assertFalse(si.in_bucket(-1, 0, 500))

    def test_compute_pass_rate_stats(self):
        self.assertEqual(si.compute_pass_rate_stats([]), (0, 0, 'N/A'))
        items = [{'verdict': 'pass'}, {'verdict': 'fail'}, {'verdict': 'pass'}]
        n, passed, rate = si.compute_pass_rate_stats(items)
        self.assertEqual(n, 3)
        self.assertEqual(passed, 2)
        self.assertEqual(rate, '66.7%')

    def test_load_driver_jobs_with_fixtures(self):
        fixtures_dir = Path(__file__).resolve().parent / 'fixtures' / 'driver_results'
        jobs = si.load_driver_jobs(fixtures_dir)
        # run_pass has 4 jobs, run_tc_on has 2 jobs, run_missing_fields has 3 jobs, run_old has 0 jobs
        self.assertEqual(len(jobs), 9)

        # Check inherited typecheck_enabled
        tc_on_jobs = [j for j in jobs if j['typecheck_enabled'] is True]
        self.assertEqual(len(tc_on_jobs), 2)
        for j in tc_on_jobs:
            self.assertEqual(j['verdict'], 'pass')

        tc_off_jobs = [j for j in jobs if j['typecheck_enabled'] is False]
        self.assertEqual(len(tc_off_jobs), 7)

    def test_qwen_job_size_lines_formatting(self):
        fixtures_dir = Path(__file__).resolve().parent / 'fixtures' / 'driver_results'
        lines = si.qwen_job_size_lines(fixtures_dir)
        text = '\n'.join(lines)
        self.assertIn('## Qwen job size', text)
        self.assertIn('### Prompt tokens', text)
        self.assertIn('### Output lines', text)
        self.assertIn('### Typecheck', text)
        self.assertIn('| <500 |', text)
        self.assertIn('| 500-999 |', text)
        self.assertIn('| 1000-1999 |', text)
        self.assertIn('| 2000+ |', text)
        self.assertIn('| on |', text)
        self.assertIn('| off |', text)

    def test_missing_results_dir_handled_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / 'nonexistent'
            jobs = si.load_driver_jobs(empty_path)
            self.assertEqual(jobs, [])
            lines = si.qwen_job_size_lines(empty_path)
            self.assertIn('No Qwen driver jobs recorded.', '\n'.join(lines))


class TestLearnedLimits(unittest.TestCase):
    def test_normalize_learned_text(self):
        self.assertEqual(si.normalize_learned_text('  Always Mock Network Calls  '), 'always mock network calls')
        self.assertEqual(si.normalize_learned_text(''), '')
        self.assertEqual(si.normalize_learned_text(None), '')

    def test_learned_limits_recurrence(self):
        now = 1000000.0
        cutoff = now - 7 * 86400

        all_records = [
            {'at': cutoff - 1000, 'task': 't1', 'learned': 'Always mock network calls', 'outcome': 'failed'},
            {'at': cutoff - 2000, 'task': 't0', 'learned': 'Use short timeouts', 'outcome': 'done'},
        ]
        week_records = [
            {'at': cutoff + 100, 'task': 't2', 'learned': 'always mock network calls', 'outcome': 'failed'},
            {'at': cutoff + 200, 'task': 't3', 'learned': 'New unique learning', 'outcome': 'done'},
            {'at': cutoff + 300, 'task': 't4', 'outcome': 'done'},  # missing learned field
        ]

        lines = si.learned_limits_lines(week_records, all_records=all_records, days=7, now=now)
        text = '\n'.join(lines)
        self.assertIn('## Learned limits', text)
        self.assertIn('- always mock network calls [recurred]', text)
        self.assertIn('- New unique learning', text)
        self.assertNotIn('New unique learning [recurred]', text)

    def test_empty_learned_limits(self):
        lines = si.learned_limits_lines([])
        self.assertIn('No learned limits recorded this week.', '\n'.join(lines))

    def test_cmd_record_with_learned_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            records_file = Path(tmp) / 'records.jsonl'
            with patch.object(si, 'RECORDS', records_file):
                parser_args = si.argparse.Namespace(
                    task='task-123', master='claude', labour='qwen',
                    outcome='done', quota_before=None, quota_after=None,
                    minutes=5.0, checks=None, blocker=[], tool='qwen',
                    reverse=False, benchmark=None, case=None,
                    learned='Check inputs before processing',
                )
                si.cmd_record(parser_args)
                records = [json.loads(line) for line in records_file.read_text().splitlines()]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['learned'], 'Check inputs before processing')


class TestTrialsListing(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.proposals = self.root / 'proposals.jsonl'
        self.patcher = patch.object(si, 'PROPOSALS', self.proposals)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_empty_trials_listing(self):
        lines = si.trials_lines([])
        self.assertIn('No active trials.', '\n'.join(lines))

    def test_trial_lifecycle_pending_then_cleanup_or_keep(self):
        # Decision date: 2026-11-01T00:00:00Z
        t0 = int(datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        # Review date will be 2026-11-08

        prop1 = {'at': t0, 'source': 'agent', 'idea': 'Split tasks', 'why': 'Fast', 'status': 'open'}
        dec1 = {
            'at': t0,
            'type': 'decision',
            'proposal': 1,
            'decision': 'adopted',
            'trial_measure': 'pass rate >= 80%',
            'trial_date': '2026-11-08',
        }
        events = [prop1, dec1]

        # Check before review date (2026-11-05): verdict should be pending
        now_before = datetime(2026, 11, 5, 0, 0, 0, tzinfo=timezone.utc)
        lines_before = si.trials_lines(events, now=now_before)
        text_before = '\n'.join(lines_before)
        self.assertIn('## Trials', text_before)
        self.assertIn('Split tasks', text_before)
        self.assertIn('pass rate >= 80%', text_before)
        self.assertIn('2026-11-08', text_before)
        self.assertIn('pending', text_before)

        # Check after review date (2026-11-09) without explicit verdict: default is clean up
        now_after = datetime(2026, 11, 9, 0, 0, 0, tzinfo=timezone.utc)
        lines_after = si.trials_lines(events, now=now_after)
        text_after = '\n'.join(lines_after)
        self.assertIn('clean up', text_after)

        # Check after review date with explicit verdict 'keep'
        dec1_kept = dict(dec1, verdict='keep')
        lines_kept = si.trials_lines([prop1, dec1_kept], now=now_after)
        self.assertIn('keep', '\n'.join(lines_kept))

    def test_non_adopted_proposals_excluded_from_trials(self):
        t0 = int(datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        prop = {'at': t0, 'source': 'agent', 'idea': 'Idea', 'why': 'Why', 'status': 'open'}
        dec_rejected = {'at': t0, 'type': 'decision', 'proposal': 1, 'decision': 'rejected', 'trial_measure': 'm'}
        lines = si.trials_lines([prop, dec_rejected])
        self.assertIn('No active trials.', '\n'.join(lines))

    def test_cmd_decide_sets_trial_measure_and_date(self):
        # Create an open proposal first
        prop = {'at': 1000, 'source': 'agent', 'idea': 'Add caching', 'why': 'Speed', 'status': 'open'}
        self.proposals.write_text(json.dumps(prop) + '\n')

        args = si.argparse.Namespace(
            index=1,
            decision='adopted',
            note='Test for 1 week',
            trial_measure='speedup >= 20%',
            trial_date=None,
            verdict=None,
        )
        si.cmd_decide(args)
        events = [json.loads(line) for line in self.proposals.read_text().splitlines()]
        self.assertEqual(len(events), 2)
        dec = events[1]
        self.assertEqual(dec['decision'], 'adopted')
        self.assertEqual(dec['trial_measure'], 'speedup >= 20%')
        # Check trial_date was calculated
        self.assertIn('trial_date', dec)
        dt = datetime.fromtimestamp(dec['at'], tz=timezone.utc) + timedelta(days=7)
        self.assertEqual(dec['trial_date'], dt.strftime('%Y-%m-%d'))


if __name__ == '__main__':
    unittest.main()
