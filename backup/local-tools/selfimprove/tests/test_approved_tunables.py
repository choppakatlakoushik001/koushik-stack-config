"""Validate shipped policy and fixture behavior using disposable copies only."""
import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import si

SOURCE = Path(__file__).resolve().parents[1]
NOW = 1793221000
WEEK = 7 * 86400


def scorer(root):
    spec = importlib.util.spec_from_file_location('tier1_scorer', root / 'benchmarks/tier1/score.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.score_case


class ApprovedTunables(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('settings', 'benchmarks'):
            shutil.copytree(SOURCE / directory, self.root / directory)
        shutil.copyfile(SOURCE / 'tunables.json', self.root / 'tunables.json')
        for name, value in dict(ROOT=self.root, TUNABLES=self.root / 'tunables.json',
                                TUNING=self.root / 'tuning.jsonl', RECORDS=self.root / 'records.jsonl',
                                PROPOSALS=self.root / 'proposals.jsonl', REPORTS=self.root / 'reports').items():
            p = patch.object(si, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.notify = patch.object(si.subprocess, 'run').start()
        self.addCleanup(patch.stopall)
        self.entries = si.catalog()['tunables']
        self.manifest = self.root / self.entries[0]['test_set']
        self.cases = json.loads((self.root / 'benchmarks/tier1/cases.json').read_text())

    def save(self, data):
        si.TUNABLES.write_text(json.dumps(data))

    def records(self, entry, trial=None, at=NOW - 10, minutes=10):
        digest, ids, _ = si.frozen_set(entry['test_set'])
        with si.RECORDS.open('a') as stream:
            for index, case in enumerate(ids):
                stream.write(json.dumps(dict(at=at, labour='qwen', outcome='done' if trial or index % 2 else 'failed',
                    minutes=minutes, tuning=dict(name=entry['name'], value=entry['current'], hash=digest,
                                               case=case, trial=trial))) + '\n')

    def values(self):
        return json.loads((self.root / 'settings/qwen.json').read_text())

    def test_approval_provenance_and_initial_values(self):
        for entry in self.entries:
            self.assertIs(entry['approved'], True)
            self.assertEqual((entry['approved_by'], entry['approved_at']), ('captain', '2026-10-28'))
            self.assertEqual(entry['adapter'], 'local_json')
            self.assertEqual(si.setting(entry)[1][entry['key']], entry['current'])
            self.assertEqual(entry['min_samples'], 30)
        retry, split = self.entries
        self.assertEqual((retry['current'], retry['min'], retry['max'], retry['step']), (3, 1, 4, 1))
        self.assertEqual((split['current'], split['start'], split['min'], split['max'], split['step']),
                         (1500, 1500, 500, 2500, 250))

    def test_pinned_manifest_covers_thirty_cases_and_ninety_assertions(self):
        digest, ids, _ = si.frozen_set(self.entries[0]['test_set'])
        manifest = json.loads(self.manifest.read_text())
        parts = [(name, (self.root / name).read_bytes()) for name in sorted(manifest['files'])]
        self.assertEqual(manifest['sha256'], si.content_fingerprint(parts))
        self.assertEqual(len(digest), 64)
        self.assertEqual(len(ids), 30)
        self.assertEqual(ids, [case['id'] for case in self.cases])
        self.assertEqual(sum(len(case['checks']) for case in self.cases), 90)
        self.assertEqual({c['id'].split('-')[0] for c in self.cases}, {'arithmetic', 'string', 'list'})

    def test_synthetic_expectations_against_reference_functions(self):
        import itertools
        references = [lambda a,b:a+b, lambda a,b:a-b, lambda a,b:a*b, lambda n:n*n,
                      abs, lambda n:n%2==0, max, min, lambda n,lo,hi:max(lo,min(hi,n)),
                      lambda n:sum(range(1,n+1)), lambda s:s[::-1], str.upper, str.lower, str.strip,
                      str.count, lambda s:s==s[::-1], lambda s:len(s.split()), lambda s:s.replace(' ','-'),
                      lambda s:s[:1], lambda s:sum(c in 'aeiou' for c in s.lower()), sum, sorted,
                      lambda xs:[x for x in xs if x%2==0], lambda xs:list(dict.fromkeys(xs)),
                      lambda xs:xs[::-1], lambda xs:[2*x for x in xs], lambda xs:sum(x>0 for x in xs),
                      lambda xs,x:xs.index(x) if x in xs else -1, lambda xs:list(itertools.accumulate(xs)),
                      lambda xss:[x for xs in xss for x in xs]]
        score = scorer(self.root)
        self.assertEqual(len(references), len(self.cases))
        for case, reference in zip(self.cases, references):
            with self.subTest(case=case['id']):
                self.assertTrue(score(case, reference))
                self.assertFalse(score(case, lambda *args: None))
        self.assertFalse(score(self.cases[5], lambda n: 1))  # bool != int contract
        self.assertFalse(score(dict(checks=[dict(args=[], expected=[1])]), lambda: [True]))
        self.assertFalse(score(self.cases[0], None))

    def test_tune_without_real_measurements_waits_and_preserves_values(self):
        before = (self.root / 'settings/qwen.json').read_bytes()
        actions = si.tune(NOW)
        self.assertEqual([a['action'] for a in actions], ['extend', 'extend'])
        self.assertEqual([a['result']['samples'] for a in actions], [0, 0])
        self.assertEqual((self.root / 'settings/qwen.json').read_bytes(), before)
        self.assertFalse(si.RECORDS.exists())

    def test_retry_trial_changes_only_proposal_and_rolls_back(self):
        self.records(self.entries[0])
        si.tune(NOW)
        trial = si.active_trial(si.read_jsonl(si.TUNING))
        self.assertEqual((trial['old'], trial['new']), (3, 4))
        self.assertEqual(self.values(), dict(fill_retry_budget=4, split_prompt_tokens=1500))
        changed = si.catalog()['tunables'][0]
        self.records(changed, trial=trial['id'], at=NOW + WEEK - 10, minutes=11)
        si.tune(NOW + WEEK)
        self.assertEqual(self.values(), dict(fill_retry_budget=3, split_prompt_tokens=1500))
        self.assertEqual(si.read_jsonl(si.TUNING)[-1]['reason'], 'guard regressed')

    def test_split_trial_uses_250_token_step_and_rolls_back(self):
        self.records(self.entries[1])
        si.tune(NOW)
        trial = si.active_trial(si.read_jsonl(si.TUNING))
        self.assertEqual((trial['old'], trial['new']), (1500, 1750))
        self.assertEqual(self.values(), dict(fill_retry_budget=3, split_prompt_tokens=1750))
        changed = si.catalog()['tunables'][1]
        self.records(changed, trial=trial['id'], at=NOW + WEEK - 10, minutes=11)
        si.tune(NOW + WEEK)
        self.assertEqual(self.values(), dict(fill_retry_budget=3, split_prompt_tokens=1500))

    def test_large_approved_step_cannot_exceed_maximum(self):
        data = si.catalog()
        entry = data['tunables'][1]
        entry['current'] = 2400
        self.save(data)
        path = self.root / 'settings/qwen.json'
        values = self.values()
        values['split_prompt_tokens'] = 2400
        path.write_text(json.dumps(values))
        self.records(entry)
        si.tune(NOW)
        self.assertEqual(self.values()['split_prompt_tokens'], 2400)
        self.assertIn('exceed approved bounds', si.read_jsonl(si.TUNING)[-1]['reason'])

    def test_changed_fixture_fails_pin_and_rolls_back_active_trial(self):
        self.records(self.entries[1])
        si.tune(NOW)
        case_file = self.root / 'benchmarks/tier1/cases.json'
        case_file.write_text(case_file.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'):
            si.frozen_set(self.entries[1]['test_set'])
        si.tune(NOW + 1)
        self.assertEqual(self.values()['split_prompt_tokens'], 1500)
        self.assertEqual(si.read_jsonl(si.TUNING)[-1]['action'], 'rollback')

    def test_forged_pin_is_refused_before_mutation(self):
        manifest = json.loads(self.manifest.read_text())
        manifest['sha256'] = '0' * 64
        self.manifest.write_text(json.dumps(manifest))
        actions = si.tune(NOW)
        self.assertTrue(all(a['action'] == 'skipped' for a in actions))
        self.assertEqual(self.values(), dict(fill_retry_budget=3, split_prompt_tokens=1500))

    def test_review_shows_both_approvals_and_steps(self):
        with patch.object(si.time, 'time', return_value=NOW), contextlib.redirect_stdout(io.StringIO()):
            si.main(['review'])
        report = next(si.REPORTS.glob('*.md')).read_text()
        for text in ('step=250', 'step=1;', 'approved by=captain on=2026-10-28',
                     'qwen_fill_retry_budget', 'qwen_split_prompt_tokens'):
            self.assertIn(text, report)
