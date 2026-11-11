import unittest
import si


class Metrics(unittest.TestCase):
    def test_complete(self):
        rows = [dict(outcome='done', minutes=2, quota_before=90, quota_after=89),
                dict(outcome='failed', minutes=4, quota_before=80, quota_after=77)]
        self.assertEqual(si.metric_value(rows, 'pass_rate'), .5)
        self.assertEqual(si.metric_value(rows, 'minutes_median'), 3)
        self.assertEqual(si.metric_value(rows, 'quota_used_median'), 2)

    def test_incomplete_and_invalid(self):
        for name in ('pass_rate', 'minutes_median', 'quota_used_median'):
            self.assertIsNone(si.metric_value([], name))
            self.assertIsNone(si.metric_value([{}], name))
        for value in (None, True, float('nan'), float('inf'), -1, '2'):
            self.assertIsNone(si.metric_value([{'minutes': value}], 'minutes_median'))
        for before, after in ((101, 90), (90, -1), (80, 90), (True, 0), (90, float('nan'))):
            self.assertIsNone(si.metric_value([dict(quota_before=before, quota_after=after)], 'quota_used_median'))
        self.assertIsNone(si.metric_value([{'outcome': 'unknown'}], 'pass_rate'))
        self.assertIsNone(si.metric_value([{'minutes': 2}, {}], 'minutes_median'))
        self.assertIsNone(si.metric_value([{}], 'unknown'))
