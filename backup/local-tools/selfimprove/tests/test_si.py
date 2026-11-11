import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import si  # noqa: E402

R = [
    {'labour': 'qwen', 'outcome': 'done', 'blockers': ['diff format'], 'minutes': 4},
    {'labour': 'qwen', 'outcome': 'failed', 'blockers': ['diff format', 'logic'], 'minutes': 10},
    {'labour': 'qwen', 'outcome': 'failed', 'blockers': [], 'minutes': None},
    {'labour': 'agy', 'outcome': 'done', 'blockers': ['logic'], 'minutes': 20},
]


class Median(unittest.TestCase):
    def test_odd_even_none_empty(self):
        self.assertEqual(si.median([3, 1, 2]), 2)
        self.assertEqual(si.median([4, 1, 3, 2]), 2.5)
        self.assertEqual(si.median([None, 5, None]), 5)
        self.assertIsNone(si.median([None]))
        self.assertIsNone(si.median([]))


class PassRate(unittest.TestCase):
    def test_rates(self):
        self.assertAlmostEqual(si.pass_rate(R, 'qwen'), 1 / 3)
        self.assertEqual(si.pass_rate(R, 'agy'), 1.0)
        self.assertIsNone(si.pass_rate(R, 'codex'))


class TopBlocker(unittest.TestCase):
    def test_most_frequent_then_alphabetical(self):
        self.assertEqual(si.top_blocker(R), ('diff format', 2))
        self.assertEqual(si.top_blocker(R[1:]), ('logic', 2))
        self.assertIsNone(si.top_blocker([{'blockers': []}]))

    def test_tie_is_alphabetical_not_first_seen(self):
        self.assertEqual(si.top_blocker([{'blockers': ['zeta']}, {'blockers': ['alpha']}]), ('alpha', 1))


class Propose(unittest.TestCase):
    """Reverse checks: start from each proposal's claim and confirm the data that
    must hold for it really does, and that no proposal appears without cause."""

    def test_low_pass_rate_and_repeat_blocker_propose(self):
        props = si.propose(si.summarize([dict(r, quota_before=None) for r in R]))
        self.assertEqual(len(props), 2)
        self.assertIn('qwen', props[0]['change'])
        self.assertIn('diff format', props[1]['change'])
        for p in props:
            self.assertTrue(p['gain'] and p['risk'] and p['check'])

    def test_healthy_week_proposes_nothing(self):
        ok = [{'labour': 'qwen', 'outcome': 'done', 'blockers': [], 'minutes': 1}] * 3
        self.assertEqual(si.propose(si.summarize(ok)), [])

    def test_never_more_than_three(self):
        many = [{'labour': f'l{i}', 'outcome': 'failed', 'blockers': ['x'], 'minutes': 1} for i in range(9)]
        self.assertLessEqual(len(si.propose(si.summarize(many))), 3)


if __name__ == '__main__':
    unittest.main()
