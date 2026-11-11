import argparse
import unittest

import si


class Nonblank(unittest.TestCase):
    def test_strips_text(self):
        self.assertEqual(si.nonblank('  measurable idea\n'), 'measurable idea')

    def test_rejects_blank(self):
        for value in ('', ' ', '\n\t'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                si.nonblank(value)
