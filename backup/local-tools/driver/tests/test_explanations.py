import unittest
from explanations import reason_line


class ExplanationTests(unittest.TestCase):
    def test_reason(self):
        self.assertEqual(reason_line('code\nREASON: because: types\nREASON: later'), 'because: types')
        self.assertEqual(reason_line('  reason: hello  \n'), 'hello')
        self.assertEqual(reason_line('no explanation'), '')
