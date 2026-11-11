import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import si


class ProposalLog(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('PROPOSALS', self.root / 'proposals.jsonl'),
                            ('TUNABLES', self.root / 'tunables.json'),
                            ('TUNING', self.root / 'tuning.jsonl'),
                            ('RECORDS', self.root / 'records.jsonl'),
                            ('REPORTS', self.root / 'reports'),
                            ('HINTS', self.root / 'hints.jsonl')]:
            patcher = patch.object(si, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.notify = patch.object(si.subprocess, 'run').start()
        self.addCleanup(patch.stopall)

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return si.main(args)

    def add(self, idea='Try smaller tasks', source='codex'):
        self.run_cli('propose', '--idea', idea, '--why', 'Reduce retries', '--source', source)

    def events(self):
        return [json.loads(line) for line in si.PROPOSALS.read_text().splitlines()]

    def report(self):
        self.run_cli('review', '--days', '1')
        return next(si.REPORTS.glob('*.md')).read_text()

    def test_propose_appends_valid_fields_and_defaults(self):
        self.add()
        first = si.PROPOSALS.read_bytes()
        self.run_cli('propose', '--idea', 'Second\nline', '--why', 'Reason', '--gain', 'Faster',
                     '--risk', 'Cost', '--check', 'Timing improves')
        self.assertTrue(si.PROPOSALS.read_bytes().startswith(first))
        one, two = self.events()
        self.assertEqual(one['status'], 'open')
        self.assertEqual(one['source'], 'codex')
        self.assertEqual(one['expected_gain'], 'not specified')
        self.assertIsInstance(one['at'], int)
        self.assertEqual(two['idea'], 'Second\nline')
        self.assertEqual((two['expected_gain'], two['risk'], two['check']),
                         ('Faster', 'Cost', 'Timing improves'))
        self.assertFalse(si.RECORDS.exists())

    def test_invalid_cli_input_never_writes(self):
        cases = [('propose', '--idea', 'x'), ('propose', '--idea', ' ', '--why', 'x'),
                 ('propose', '--idea', 'x', '--why', 'x', '--risk', ' '),
                 ('propose', '--idea', 'x', '--why', 'x', '--unknown', 'x'),
                 ('decide', '1', 'wrong'), ('decide', 'nan', 'later')]
        for args in cases:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli(*args)
        self.assertFalse(si.PROPOSALS.exists())

    def test_decisions_are_append_only_and_latest_wins(self):
        self.add()
        before = si.PROPOSALS.read_bytes()
        for decision in ('adopted', 'rejected', 'later'):
            self.run_cli('decide', '1', decision, '--note', 'Captain says wait')
        self.assertTrue(si.PROPOSALS.read_bytes().startswith(before))
        events = self.events()
        self.assertEqual(len(events), 4)
        self.assertEqual([e['decision'] for e in events[1:]], ['adopted', 'rejected', 'later'])
        self.assertTrue(all(e['proposal'] == 1 for e in events[1:]))
        text = self.report()
        self.assertIn('Latest decision: later — Captain says wait', text)
        self.assertNotIn('Latest decision: adopted', text)
        self.assertEqual(self.events(), events)

    def test_indexes_ignore_decisions_and_review_sort_order(self):
        rows = [dict(at=20, source='z', idea='Newer', why='Why', expected_gain='Gain',
                     risk='Risk', check='Proof', status='building'),
                dict(at=10, source='z', idea='Older', status='info'),
                dict(at=1, source='a', idea='Other source', status='open'),
                dict(at=1, source='z', idea='Hidden', status='closed')]
        si.PROPOSALS.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.run_cli('decide', '1', 'adopted')
        self.add('Fifth', 'z')
        self.run_cli('decide', '5', 'rejected')
        before = si.PROPOSALS.read_bytes()
        text = self.report()
        self.assertIn("## Proposal log (for the captain's decision)", text)
        self.assertLess(text.index('### a'), text.index('### z'))
        self.assertLess(text.index('2. Older'), text.index('1. Newer'))
        self.assertNotIn('Hidden', text)
        for field in ('Why: Why', 'Expected gain: Gain', 'Risk: Risk', 'Proving check: Proof',
                      'Latest decision: adopted', '5. Fifth', 'Latest decision: rejected'):
            self.assertIn(field, text)
        self.assertEqual(si.PROPOSALS.read_bytes(), before)

    def test_invalid_index_and_corrupt_log_never_append(self):
        self.add()
        before = si.PROPOSALS.read_bytes()
        for index in ('0', '-1', '2'):
            self.assertEqual(self.run_cli('decide', index, 'adopted'), 1)
            self.assertEqual(si.PROPOSALS.read_bytes(), before)
        si.PROPOSALS.write_text('{broken\n')
        self.assertEqual(self.run_cli('propose', '--idea', 'x', '--why', 'y'), 1)
        self.assertEqual(si.PROPOSALS.read_text(), '{broken\n')

    def test_missing_log_empty_review(self):
        self.assertIn('No open, building or info proposals.', self.report())
        self.assertFalse(si.PROPOSALS.exists())

    def test_existing_log_without_trailing_newline(self):
        self.add()
        si.PROPOSALS.write_text(si.PROPOSALS.read_text().rstrip('\n'))
        self.add('Second')
        self.assertEqual(len(self.events()), 2)

    def test_concurrent_appends_allocate_unique_indexes(self):
        def append(_):
            return si.append_proposal_event(lambda proposals, decisions: (
                {'at': 1, 'source': 'test', 'idea': 'Concurrent idea', 'why': 'Test',
                 'status': 'open'}, len(proposals) + 1))
        with ThreadPoolExecutor(max_workers=4) as workers:
            indexes = list(workers.map(append, range(12)))
        self.assertEqual(sorted(indexes), list(range(1, 13)))
        self.assertEqual(len(self.events()), 12)
