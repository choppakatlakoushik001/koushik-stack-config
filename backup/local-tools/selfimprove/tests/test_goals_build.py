import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import si
from tests.mutation_helper import run_mutation_test

WEEK = 7 * 86400
DAY = 86400
NOW = 1793200000


class TestProposal17Scorecard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('RECORDS', self.root / 'records.jsonl'),
                            ('PROPOSALS', self.root / 'proposals.jsonl'),
                            ('TUNABLES', self.root / 'tunables.json'),
                            ('TUNING', self.root / 'tuning.jsonl'),
                            ('FINDINGS_DEFAULT', self.root / 'findings.jsonl'),
                            ('REPORTS', self.root / 'reports')]:
            p = patch.object(si, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.notify = patch.object(si.subprocess, 'run').start()
        self.addCleanup(patch.stopall)

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return si.main(args)

    def report(self):
        self.run_cli('review', '--days', '7')
        return next(si.REPORTS.glob('*.md')).read_text()

    def test_scorecard_has_all_goals_and_tools_and_flagged_when_empty(self):
        text = self.report()
        self.assertIn('## Goal scorecard', text)
        self.assertIn('### Maintainer proposals', text)
        for goal in si.SCORECARD_GOALS:
            self.assertIn(f"- {goal}:", text)
            # When empty, should print none for evidence and FLAGGED
            row_match = f"| {goal} | none |"
            self.assertIn(row_match, text)
            self.assertIn("FLAGGED |", text)
        for tool in si.SCORECARD_TOOLS:
            self.assertIn(tool, text)

    def test_scorecard_records_and_proposals_evidence(self):
        # Record with tool Codex
        self.run_cli('record', '--task', 'T1', '--master', 'agy', '--labour', 'codex',
                     '--tool', 'Codex', '--outcome', 'done', '--minutes', '5')
        # Proposal tagged with goal advanced and tool Codex
        self.run_cli('propose', '--idea', 'Codex caching', '--why', 'Faster',
                     '--goal', 'advanced', '--tool', 'Codex')

        text = self.report()
        # Row for advanced should have Codex with evidence, and other tools in missing
        adv_row = next(line for line in text.splitlines() if line.startswith('| advanced |'))
        self.assertIn('Codex (1 tasks (100% done); prop #1)', adv_row)
        self.assertIn('Grok', adv_row)

    def test_record_tool_defaults_to_qwen_backward_compatible(self):
        # Record without --tool
        self.run_cli('record', '--task', 'T2', '--master', 'agy', '--labour', 'qwen',
                     '--outcome', 'done')
        records = [json.loads(line) for line in si.RECORDS.read_text().splitlines()]
        self.assertEqual(records[0]['tool'], 'qwen')

        # Existing record in file without 'tool' field should be read as 'qwen'
        legacy_rec = {'at': int(time.time()), 'task': 'T3', 'master': 'agy',
                      'labour': 'qwen', 'outcome': 'done'}
        with si.RECORDS.open('a') as f:
            f.write(json.dumps(legacy_rec) + '\n')
        loaded = si.load(7)
        self.assertEqual(loaded[1].get('tool', 'qwen'), 'qwen')

    def test_scorecard_hard_cap(self):
        # Verify max_lines cap truncates table
        lines = si.goal_scorecard_lines([], [], max_lines=8)
        self.assertLessEqual(len(lines), 35)  # 8 table lines + maintainer lines
        self.assertTrue(any('[truncated]' in line for line in lines))


class TestProposal20LoopHealth(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('PROPOSALS', self.root / 'proposals.jsonl'),
                            ('TUNING', self.root / 'tuning.jsonl'),
                            ('RECORDS', self.root / 'records.jsonl'),
                            ('TUNABLES', self.root / 'tunables.json'),
                            ('REPORTS', self.root / 'reports')]:
            p = patch.object(si, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.notify = patch.object(si.subprocess, 'run').start()
        self.addCleanup(patch.stopall)

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return si.main(args)

    def test_empty_loop_health(self):
        m = si.loop_health_metrics(NOW)
        self.assertEqual(m['proposals_logged'], 0)
        self.assertEqual(m['proposals_decided'], 0)
        self.assertEqual(m['proposals_adopted'], 0)
        self.assertEqual(m['adoption_rate'], 0.0)
        self.assertIsNone(m['median_days_to_decision'])
        self.assertEqual(m['kept_after_28_days'], 0)

    def test_loop_health_metrics_computation(self):
        # Write proposals and decisions
        t0 = NOW - 10 * DAY
        p1 = {'at': t0, 'source': 'agent', 'idea': 'Idea 1', 'why': 'W1', 'status': 'open'}
        p2 = {'at': t0, 'source': 'agent', 'idea': 'Idea 2', 'why': 'W2', 'status': 'open'}
        p3 = {'at': t0, 'source': 'agent', 'idea': 'Idea 3', 'why': 'W3', 'status': 'open'}
        # Decision 1: adopted 2 days later
        d1 = {'at': t0 + 2 * DAY, 'type': 'decision', 'proposal': 1, 'decision': 'adopted'}
        # Decision 2: rejected 4 days later
        d2 = {'at': t0 + 4 * DAY, 'type': 'decision', 'proposal': 2, 'decision': 'rejected'}

        for event in (p1, p2, p3, d1, d2):
            with si.PROPOSALS.open('a') as f:
                f.write(json.dumps(event) + '\n')

        m = si.loop_health_metrics(NOW)
        self.assertEqual(m['proposals_logged'], 3)
        self.assertEqual(m['proposals_decided'], 2)
        self.assertEqual(m['proposals_adopted'], 1)
        self.assertAlmostEqual(m['adoption_rate'], 1 / 3)
        # Median of 2 days and 4 days = 3.0 days
        self.assertAlmostEqual(m['median_days_to_decision'], 3.0)

    def test_loop_health_28_day_retention(self):
        t_old = NOW - 35 * DAY
        # Adopted decision 35 days ago
        p1 = {'at': t_old - DAY, 'source': 'agent', 'idea': 'Old Idea', 'why': 'W', 'status': 'open'}
        d1 = {'at': t_old, 'type': 'decision', 'proposal': 1, 'decision': 'adopted'}
        with si.PROPOSALS.open('a') as f:
            f.write(json.dumps(p1) + '\n' + json.dumps(d1) + '\n')

        # Tuning keep 30 days ago
        k1 = {'at': NOW - 30 * DAY, 'week': '2026-W30', 'action': 'keep', 'name': 'budget', 'reason': 'gain'}
        with si.TUNING.open('a') as f:
            f.write(json.dumps(k1) + '\n')

        m = si.loop_health_metrics(NOW)
        self.assertEqual(m['kept_after_28_days'], 2)

        # If later rolled back, retention count decreases
        r1 = {'at': NOW - 5 * DAY, 'week': '2026-W34', 'action': 'rollback', 'name': 'budget', 'reason': 'regressed'}
        with si.TUNING.open('a') as f:
            f.write(json.dumps(r1) + '\n')

        m2 = si.loop_health_metrics(NOW)
        self.assertEqual(m2['kept_after_28_days'], 1)

    def test_loop_health_in_review_output(self):
        self.run_cli('propose', '--idea', 'Test idea', '--why', 'Testing')
        self.run_cli('decide', '1', 'adopted')
        self.run_cli('review', '--days', '7')
        report_text = next(si.REPORTS.glob('*.md')).read_text()
        self.assertIn('## Loop health', report_text)
        self.assertIn('- Proposals logged: 1', report_text)
        self.assertIn('- Proposals decided: 1', report_text)
        self.assertIn('- Proposals adopted: 1', report_text)
        self.assertIn('- Proposal adoption rate: 100.0%', report_text)


class TestProposal19ReverseCheck(unittest.TestCase):
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
             outcomes=('done', 'done'), minutes=10, cases=('a', 'b'), digest=None, reverse=False):
        digest = digest or si.frozen_set('suite.json')[0]
        with si.RECORDS.open('a') as stream:
            for case, outcome in zip(cases, outcomes):
                rec = dict(at=at, labour='qwen', outcome=outcome, minutes=minutes,
                           tuning=dict(name=name, value=value, trial=trial, hash=digest, case=case))
                if reverse:
                    rec['tuning']['reverse'] = True
                stream.write(json.dumps(rec) + '\n')

    def events(self):
        return si.read_jsonl(si.TUNING)

    def value(self):
        return json.loads((self.root / 'settings.json').read_text())['budget']

    def test_reverse_check_keeps_when_gain_disappears(self):
        """When old value does not achieve trial gain (target drops under old value), change is kept."""
        si.tune(NOW)
        trial = self.events()[-1]['trial']
        # New value (3) achieves 100% pass rate
        self.rows(trial=trial['id'], value=3, outcomes=('done', 'done'), minutes=9)
        # Reverse check on old value (2) achieves only 50% pass rate (gain disappears)
        self.rows(trial=trial['id'], value=2, outcomes=('done', 'failed'), minutes=9, reverse=True)

        si.tune(NOW + WEEK)
        last_event = self.events()[-1]
        self.assertEqual(last_event['action'], 'keep')
        self.assertEqual(self.value(), 3)
        # Both result and reverse must be recorded in tuning.jsonl
        self.assertIn('result', last_event)
        self.assertIn('reverse', last_event)
        self.assertEqual(last_event['result']['target'], 1.0)
        self.assertEqual(last_event['reverse']['target'], 0.5)

    def test_reverse_check_rolls_back_unattributed_when_gain_persists(self):
        """When old value ALSO achieves the gain, the gain was not caused by the setting change.
        It is marked 'unattributed' and rolled back."""
        si.tune(NOW)
        trial = self.events()[-1]['trial']
        # New value (3) achieves 100% pass rate
        self.rows(trial=trial['id'], value=3, outcomes=('done', 'done'), minutes=9)
        # Reverse measurement on old value (2) ALSO achieves 100% pass rate!
        self.rows(trial=trial['id'], value=2, outcomes=('done', 'done'), minutes=9, reverse=True)

        si.tune(NOW + WEEK)
        last_event = self.events()[-1]
        self.assertEqual(last_event['action'], 'rollback')
        self.assertEqual(last_event['reason'], 'unattributed')
        self.assertEqual(self.value(), 2)  # Rolled back to old value
        # Both result and reverse must be recorded in tuning.jsonl
        self.assertIn('result', last_event)
        self.assertIn('reverse', last_event)
        self.assertEqual(last_event['result']['target'], 1.0)
        self.assertEqual(last_event['reverse']['target'], 1.0)


class TestProposal18Scan(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('WATCHED', self.root / 'watched.json'),
                            ('ADVANCEMENTS', self.root / 'advancements.jsonl'),
                            ('PROPOSALS', self.root / 'proposals.jsonl')]:
            p = patch.object(si, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_scan_no_fetch_does_not_call_fetcher(self):
        si.WATCHED.write_text(json.dumps([{'name': 'tool1', 'command': 'fake'}]))
        called = False

        def fake_fetcher(t):
            nonlocal called
            called = True
            return {'type': 'version', 'output': '1.0'}

        res = si.scan(fetch=False, fetcher=fake_fetcher)
        self.assertFalse(called)
        self.assertEqual(len(res['tools']), 1)
        self.assertEqual(len(res['new_items']), 0)
        self.assertFalse(si.ADVANCEMENTS.exists())

    def test_scan_version_advancement_creates_proposal_and_deduplicates(self):
        si.WATCHED.write_text(json.dumps([{'name': 'agy', 'command': 'agy --version'}]))

        def fake_fetcher(t):
            return {'type': 'version', 'output': 'agy v2.4.0'}

        # First scan detects new version
        res1 = si.scan(fetch=True, fetcher=fake_fetcher)
        self.assertEqual(len(res1['new_items']), 1)
        item = res1['new_items'][0]
        self.assertEqual(item['tool'], 'agy')
        self.assertEqual(item['item'], 'agy v2.4.0')
        self.assertEqual(item['type'], 'version')
        self.assertEqual(item['proposal'], 1)

        # Check proposal was created in proposals.jsonl
        props = [json.loads(line) for line in si.PROPOSALS.read_text().splitlines()]
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]['goal'], 'advanced')
        self.assertIn('agy v2.4.0', props[0]['idea'])

        # Second scan with same version finds no new items
        res2 = si.scan(fetch=True, fetcher=fake_fetcher)
        self.assertEqual(len(res2['new_items']), 0)
        props2 = [json.loads(line) for line in si.PROPOSALS.read_text().splitlines()]
        self.assertEqual(len(props2), 1)

    def test_scan_changelog_headings(self):
        si.WATCHED.write_text(json.dumps([{'name': 'claude', 'changelog_url': 'https://example.com'}]))
        changelog = """
# Claude 3.8 Opus Released
Some description.
## Prompt Caching Enhancements
Details.
"""

        def fake_fetcher(t):
            return {'type': 'changelog', 'output': changelog}

        res = si.scan(fetch=True, fetcher=fake_fetcher)
        self.assertEqual(len(res['new_items']), 2)
        headings = [item['item'] for item in res['new_items']]
        self.assertIn('Claude 3.8 Opus Released', headings)
        self.assertIn('Prompt Caching Enhancements', headings)

    def test_tracked_watched_json_is_valid(self):
        """Verify the repo's watched.json file is valid JSON with required tool definitions."""
        data = json.loads(si.ROOT.joinpath('watched.json').read_text())
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 6)
        names = {t['name'] for t in data}
        self.assertIn('qwen', names)
        self.assertIn('claude', names)
        self.assertIn('agy', names)


class TestProposal19MutationTesting(unittest.TestCase):
    def test_mutation_charter_status_comparison(self):
        """Flipping `!= 'built'` to `== 'built'` in charter_open_rows must fail test_repo_charter_open_rows."""
        killed = run_mutation_test(
            module_path=si.ROOT / 'si.py',
            target_pattern="r['base_status'] != 'built'",
            replacement="r['base_status'] == 'built'",
            test_module_or_args=['tests.test_charter.TestCharter.test_repo_charter_open_rows']
        )
        self.assertTrue(killed)

    def test_mutation_reverse_gain_disappeared_comparison(self):
        """Flipping `<` to `>=` in reverse gain_disappeared check must fail test_reverse_check_keeps_when_gain_disappears."""
        killed = run_mutation_test(
            module_path=si.ROOT / 'si.py',
            target_pattern="reverse['target'] < result['target']",
            replacement="reverse['target'] >= result['target']",
            test_module_or_args=['tests.test_goals_build.TestProposal19ReverseCheck.test_reverse_check_keeps_when_gain_disappears']
        )
        self.assertTrue(killed)

    def test_mutation_unattributed_reason_constant(self):
        """Mutating `reason = 'unattributed'` to `reason = 'ok'` must fail test_reverse_check_rolls_back_unattributed."""
        killed = run_mutation_test(
            module_path=si.ROOT / 'si.py',
            target_pattern="reason = 'unattributed'",
            replacement="reason = 'ok'",
            test_module_or_args=['tests.test_goals_build.TestProposal19ReverseCheck.test_reverse_check_rolls_back_unattributed_when_gain_persists']
        )
        self.assertTrue(killed)


if __name__ == '__main__':
    unittest.main()
