import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import si

WEEK = 7 * 86400
NOW = 1793200000


class Tuning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in dict(ROOT=self.root, TUNABLES=self.root / 'tunables.json',
                                TUNING=self.root / 'tuning.jsonl', RECORDS=self.root / 'records.jsonl',
                                PROPOSALS=self.root / 'proposals.jsonl', REPORTS=self.root / 'reports').items():
            p = patch.object(si, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.notify = patch.object(si.subprocess, 'run').start()
        self.addCleanup(patch.stopall)
        self.write('settings.json', {'budget': 2, 'unrelated': 'preserve me'})
        (self.root / 'benchmark.txt').write_text('unchanging tests and protocol')
        self.write('suite.json', {'cases': ['a', 'b'], 'files': ['benchmark.txt']})
        self.entry = dict(name='budget', approved=True, adapter='local_json', file='settings.json',
                          key='budget', current=2, min=1, max=4, step=1,
                          target=dict(name='pass_rate', direction='higher'),
                          guard=dict(name='minutes_median', direction='lower'),
                          min_samples=2, test_set='suite.json')
        self.save()
        self.baseline()

    def write(self, file, data):
        (self.root / file).write_text(json.dumps(data))

    def save(self, *others):
        self.write('tunables.json', dict(version=1, tunables=[self.entry, *others]))

    def baseline(self, name='budget', value=2):
        self.rows(name=name, value=value, trial=None, at=NOW - 10, outcomes=('done', 'failed'))

    def rows(self, name='budget', value=3, trial=None, at=NOW + WEEK - 10,
             outcomes=('done', 'done'), minutes=10, cases=('a', 'b'), digest=None):
        digest = digest or si.frozen_set('suite.json')[0]
        with si.RECORDS.open('a') as stream:
            for case, outcome in zip(cases, outcomes):
                stream.write(json.dumps(dict(at=at, labour='qwen', outcome=outcome, minutes=minutes,
                    tuning=dict(name=name, value=value, trial=trial, hash=digest, case=case))) + '\n')

    def events(self):
        return si.read_jsonl(si.TUNING)

    def value(self):
        return json.loads((self.root / 'settings.json').read_text())['budget']

    def start(self):
        si.tune(NOW)
        return self.events()[-1]['trial']

    def finish(self, **kwargs):
        trial = self.start()
        self.rows(trial=trial['id'], **kwargs)
        before = si.TUNING.read_bytes()
        si.tune(NOW + WEEK)
        self.assertTrue(si.TUNING.read_bytes().startswith(before))
        return self.events()[-1]

    def test_unapproved_never_changes_setting(self):
        self.entry['approved'] = False
        self.save()
        before = (self.root / 'settings.json').read_bytes(), si.TUNABLES.read_bytes()
        si.tune(NOW)
        self.assertEqual(before, ((self.root / 'settings.json').read_bytes(), si.TUNABLES.read_bytes()))
        self.assertEqual(self.events()[-1]['reason'], 'not captain approved')

    def test_bounds_allowed_set_and_invalid_policy(self):
        for change in (dict(max=2), dict(allowed=[2, 4]), dict(current=9), dict(step=0), dict(step=True),
                       dict(min_samples=1), dict(approved='true')):
            with self.subTest(change=change):
                original = dict(self.entry)
                self.entry.update(change)
                self.save()
                si.tune(NOW)
                self.assertEqual(self.value(), 2)
                self.assertFalse(any(e['action'] == 'trial_start' for e in self.events()))
                self.entry = original

    def test_start_names_metrics_and_freezes_baseline_before_mutation(self):
        trial = self.start()
        self.assertEqual((trial['old'], trial['new'], self.value()), (2, 3, 3))
        self.assertEqual(trial['baseline']['target'], .5)
        self.assertEqual(trial['entry']['guard']['name'], 'minutes_median')
        self.assertEqual(len(trial['hash']), 64)
        self.assertEqual(si.catalog()['tunables'][0]['current'], 3)
        self.assertEqual(json.loads((self.root / 'settings.json').read_text())['unrelated'], 'preserve me')

    def test_guard_regression_rolls_back_despite_better_target(self):
        final = self.finish(minutes=11)
        self.assertEqual((final['action'], final['reason'], self.value()), ('rollback', 'guard regressed', 2))
        self.assertEqual(si.catalog()['tunables'][0]['current'], 2)
        self.assertEqual(self.events()[-2]['action'], 'rollback_pending')

    def test_target_tie_or_regression_rolls_back(self):
        final = self.finish(outcomes=('done', 'failed'))
        self.assertEqual((final['action'], self.value()), ('rollback', 2))

    def test_improvement_kept(self):
        final = self.finish(minutes=9)
        self.assertEqual((final['action'], self.value()), ('keep', 3))
        self.assertIsNone(si.active_trial(self.events()))

    def test_downward_step_and_allowed_set(self):
        self.entry.pop('min')
        self.entry.pop('max')
        self.entry.update(allowed=[1, 2], step=-1)
        self.save()
        trial = self.start()
        self.assertEqual((trial['old'], trial['new'], self.value()), (2, 1, 1))

    def test_candidate_adapter_cannot_be_activated_by_approval_alone(self):
        self.entry['adapter'] = 'candidate_only'
        self.save()
        si.tune(NOW)
        self.assertEqual(self.value(), 2)
        self.assertIn('candidate only', self.events()[-1]['reason'])

    def test_hash_change_voids_immediately(self):
        self.start()
        (self.root / 'benchmark.txt').write_text('easier tests')
        si.tune(NOW + 1)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('rollback', 2))
        self.assertIn('hash changed', self.events()[-1]['reason'])

    def test_missing_input_voids_immediately(self):
        self.start()
        (self.root / 'benchmark.txt').unlink()
        si.tune(NOW + 1)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('rollback', 2))
        self.assertIn('unavailable', self.events()[-1]['reason'])

    def test_manifest_change_voids(self):
        self.start()
        self.write('suite.json', {'cases': ['a'], 'files': ['benchmark.txt']})
        si.tune(NOW + 1)
        self.assertIn('hash changed', self.events()[-1]['reason'])
        self.assertEqual(self.value(), 2)

    def test_small_sample_extends_and_duplicates_do_not_count(self):
        trial = self.start()
        self.rows(trial=trial['id'], cases=('a', 'a'))
        si.tune(NOW + WEEK)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('extend', 3))
        self.assertEqual(self.events()[-1]['result']['samples'], 1)
        self.rows(trial=trial['id'], cases=('b',), outcomes=('done',), at=NOW + WEEK + 1)
        si.tune(NOW + 2 * WEEK)
        self.assertEqual(self.events()[-1]['action'], 'keep')

    def test_waits_seven_days_even_with_enough_samples(self):
        trial = self.start()
        self.rows(trial=trial['id'], at=NOW + 5)
        si.tune(NOW + 10)
        self.assertEqual(self.events()[-1]['action'], 'waiting')
        self.assertEqual(self.value(), 3)

    def test_wrong_hash_value_trial_and_old_records_excluded(self):
        trial = self.start()
        self.rows(trial='wrong')
        self.rows(trial=trial['id'], digest='wrong')
        self.rows(trial=trial['id'], value=2)
        self.rows(trial=trial['id'], at=NOW - 1)
        si.tune(NOW + WEEK)
        self.assertEqual(self.events()[-1]['action'], 'extend')
        self.assertEqual(self.events()[-1]['result']['samples'], 0)

    def test_one_trial_at_a_time_and_weekly_limit(self):
        second = dict(self.entry, name='other', file='other.json')
        self.write('other.json', {'budget': 2})
        self.save(second)
        self.baseline(name='other')
        trial = self.start()
        si.tune(NOW + 1)
        self.assertEqual(sum(e['action'] == 'trial_start' for e in self.events()), 1)
        self.rows(trial=trial['id'], at=NOW + WEEK - 10)
        si.tune(NOW + WEEK)
        si.tune(NOW + WEEK + 1)
        self.assertEqual(sum(e['action'] == 'trial_start' for e in self.events()), 1)
        self.assertEqual(json.loads((self.root / 'other.json').read_text())['budget'], 2)

    def test_concurrent_tune_starts_only_once(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(si.tune, [NOW, NOW]))
        self.assertEqual(sum(e['action'] == 'trial_start' for e in self.events()), 1)

    def test_approval_revocation_restores_original_value(self):
        self.start()
        data = si.catalog()
        data['tunables'][0]['approved'] = False
        self.write('tunables.json', data)
        si.tune(NOW + 1)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('rollback', 2))

    def test_failed_start_is_recovered_from_write_ahead_log(self):
        with patch.object(si, 'apply_value', side_effect=OSError('simulated power loss')):
            with self.assertRaises(OSError):
                si.tune(NOW)
        self.assertEqual(self.events()[-1]['action'], 'trial_start')
        self.assertEqual(self.value(), 2)
        si.tune(NOW + 1)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('rollback', 2))

    def test_interrupted_rollback_resumes(self):
        trial = self.start()
        self.rows(trial=trial['id'], minutes=11)
        with patch.object(si, 'apply_value', side_effect=OSError('interruption')):
            with self.assertRaises(OSError):
                si.tune(NOW + WEEK)
        self.assertEqual(self.events()[-1]['action'], 'rollback_pending')
        si.tune(NOW + WEEK + 1)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('rollback', 2))

    def test_external_setting_edit_is_not_overwritten(self):
        self.start()
        self.write('settings.json', {'budget': 99})
        with self.assertRaisesRegex(ValueError, 'manual reconciliation'):
            si.tune(NOW + 1)
        self.assertEqual(self.value(), 99)
        self.assertEqual(self.events()[-1]['action'], 'rollback_pending')

    def test_rejected_step_does_not_oscillate(self):
        self.finish(minutes=11)
        self.rows(value=2, trial=None, at=NOW + 2 * WEEK - 10)
        si.tune(NOW + 2 * WEEK)
        self.assertIn('already tried', self.events()[-1]['reason'])
        self.assertEqual(self.value(), 2)

    def test_path_escape_and_symlink_refused(self):
        for file in ('../settings.json', str(self.root / 'settings.json'), '.git/config'):
            self.entry['file'] = file
            self.save()
            si.tune(NOW)
            self.assertEqual(self.events()[-1]['action'], 'skipped')
        (self.root / 'link.json').symlink_to(self.root / 'settings.json')
        self.entry['file'] = 'link.json'
        self.save()
        si.tune(NOW)
        self.assertIn('symlinks', self.events()[-1]['reason'])

    def test_no_baseline_no_trial(self):
        si.RECORDS.unlink()
        si.tune(NOW)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('extend', 2))

    def test_incomplete_metric_extends_without_dropping_failed_case(self):
        trial = self.start()
        self.rows(trial=trial['id'], minutes=None)
        si.tune(NOW + WEEK)
        self.assertEqual(self.events()[-1]['action'], 'extend')
        self.assertEqual(self.events()[-1]['result']['samples'], 2)
        self.assertEqual(self.value(), 3)

    def test_declared_minimum_cannot_be_reduced_by_small_manifest(self):
        self.entry['min_samples'] = 3
        self.save()
        si.tune(NOW)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('extend', 2))

    def test_policy_edit_voids_and_restores_trial(self):
        self.start()
        data = si.catalog()
        data['tunables'][0]['max'] = 2
        self.write('tunables.json', data)
        si.tune(NOW + 1)
        self.assertEqual((self.events()[-1]['action'], self.value()), ('rollback', 2))
        self.assertIn('policy changed', self.events()[-1]['reason'])

    def test_second_tunable_can_start_next_week_with_fresh_baseline(self):
        second = dict(self.entry, name='other', file='other.json')
        self.write('other.json', {'budget': 2})
        self.save(second)
        trial = self.start()
        self.rows(trial=trial['id'])
        si.tune(NOW + WEEK)
        self.rows(name='other', value=2, trial=None, at=NOW + 2 * WEEK - 10,
                  outcomes=('done', 'failed'))
        si.tune(NOW + 2 * WEEK)
        active = si.active_trial(self.events())
        self.assertEqual(active['entry']['name'], 'other')

    def test_malformed_manifest_is_a_safe_skip(self):
        self.write('suite.json', [])
        si.tune(NOW)
        self.assertEqual(self.events()[-1]['action'], 'skipped')
        self.assertEqual(self.value(), 2)

    def test_review_runs_tune_and_includes_bounds_and_actions(self):
        with patch.object(si.time, 'time', return_value=NOW), contextlib.redirect_stdout(io.StringIO()):
            si.main(['review'])
        report = next(si.REPORTS.glob('*.md')).read_text()
        for text in ('## Tier 1 tuning', 'bounds=[1, 4]', 'trial_start', 'pass_rate', 'minutes_median', 'frozen hash'):
            self.assertIn(text, report)
        self.notify.assert_called_once()

    def test_record_tags_actual_case_hash_value_and_trial(self):
        with patch.object(si.time, 'time', return_value=NOW - 1), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(si.main(['record', '--task', 'x', '--master', 'codex', '--labour', 'qwen',
                                      '--outcome', 'done', '--minutes', '10', '--benchmark', 'budget', '--case', 'a']))
        tag = si.read_jsonl(si.RECORDS)[-1]['tuning']
        self.assertEqual((tag['name'], tag['case'], tag['value'], tag['trial']), ('budget', 'a', 2, None))
        self.assertEqual(tag['hash'], si.frozen_set('suite.json')[0])
        trial = self.start()
        with patch.object(si.time, 'time', return_value=NOW + 1), contextlib.redirect_stdout(io.StringIO()):
            si.main(['record', '--task', 'x', '--master', 'codex', '--labour', 'qwen', '--outcome', 'done',
                     '--minutes', '10', '--benchmark', 'budget', '--case', 'a'])
        self.assertEqual(si.read_jsonl(si.RECORDS)[-1]['tuning']['trial'], trial['id'])

    def test_bad_benchmark_flags_never_append(self):
        before = si.RECORDS.read_bytes()
        for args in (['--benchmark', 'budget'], ['--case', 'a'], ['--benchmark', 'budget', '--case', 'unknown']):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(si.main(['record', '--task', 'x', '--master', 'codex', '--labour', 'qwen', '--outcome', 'done', *args]), 1)
            self.assertEqual(si.RECORDS.read_bytes(), before)
