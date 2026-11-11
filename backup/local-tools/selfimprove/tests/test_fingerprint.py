import hashlib
import unittest
import si


class Fingerprint(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(si.content_fingerprint([]), hashlib.sha256(b'').hexdigest())

    def test_framing_unicode_and_order(self):
        parts = [('α', b'abc'), ('x', b'\x00\xff')]
        framed = b''
        for label, content in parts:
            for chunk in (label.encode('utf-8'), content):
                framed += len(chunk).to_bytes(8, 'big') + chunk
        self.assertEqual(si.content_fingerprint(parts), hashlib.sha256(framed).hexdigest())
        self.assertNotEqual(si.content_fingerprint(parts), si.content_fingerprint(list(reversed(parts))))
        self.assertNotEqual(si.content_fingerprint([('ab', b'c')]), si.content_fingerprint([('a', b'bc')]))
