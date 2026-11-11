"""Tests for no-op notification wake measurement module and CLI."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import noop
import si
from tests.fixtures.synthetic_wake_session import SYNTHETIC_WAKE_SESSION


class TestNoopWakeMeasure(unittest.TestCase):
    def test_extract_text_and_source_key(self):
        wake_user = "<task-notification>\n<summary>background build finished</summary>\nJob 42\n</task-notification>"
        self.assertTrue(noop.is_wake_notification(wake_user))
        self.assertEqual(noop.extract_source_key(wake_user), "background build finished")

        wake_marker = "Stop hook feedback: process /tmp/123 exited"
        self.assertTrue(noop.is_wake_notification(wake_marker))
        self.assertEqual(noop.extract_source_key(wake_marker), "Stop hook feedback")

        wake_sys = "[SYSTEM NOTIFICATION - NOT USER INPUT] Watcher /var/log/app.log"
        self.assertTrue(noop.is_wake_notification(wake_sys))
        self.assertEqual(noop.extract_source_key(wake_sys), "SYSTEM NOTIFICATION")

        user_typed = "Can you check the logs?"
        self.assertFalse(noop.is_wake_notification(user_typed))

    def test_is_noop(self):
        self.assertTrue(noop.is_noop_response("Captain, shipshape."))
        self.assertTrue(noop.is_noop_response("  Captain, shipshape. \n"))
        self.assertTrue(noop.is_noop_response(""))
        self.assertTrue(noop.is_noop_response("   \n  "))
        self.assertFalse(noop.is_noop_response("Working on it now."))

    def test_measure_transcripts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "session.jsonl"
            f.write_text(SYNTHETIC_WAKE_SESSION)

            res = noop.measure_transcripts([f])
            self.assertEqual(res['summary']['total_wakes'], 3)
            self.assertEqual(res['summary']['total_noop_wakes'], 2)
            self.assertAlmostEqual(res['summary']['noop_share'], 2 / 3, places=2)
            self.assertEqual(res['summary']['noop_cache_read_tokens'], 1800)
            self.assertEqual(res['summary']['noop_other_tokens'], 265)

            # Check days breakdown
            self.assertIn('2026-11-01', res['by_day'])
            day_data = res['by_day']['2026-11-01']
            self.assertEqual(day_data['wakes'], 3)
            self.assertEqual(day_data['noop_wakes'], 2)
            self.assertEqual(day_data['noop_cache_read_tokens'], 1800)
            self.assertEqual(day_data['noop_other_tokens'], 265)

            # Check sources breakdown
            self.assertIn('background build finished', res['by_source'])
            self.assertEqual(res['by_source']['background build finished']['wakes'], 1)
            self.assertEqual(res['by_source']['background build finished']['noop_wakes'], 1)

    def test_measure_transcripts_days_filter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "session.jsonl"
            # Past date
            lines = [
                json.dumps({
                    "type": "user",
                    "timestamp": "2020-01-01T00:00:00Z",
                    "message": {"content": "<task-notification>\n<summary>old</summary></task-notification>"},
                }),
                json.dumps({
                    "type": "assistant",
                    "timestamp": "2020-01-01T00:00:05Z",
                    "message": {"content": "Captain, shipshape.", "usage": {"input_tokens": 10, "cache_read_input_tokens": 50}},
                }),
            ]
            f.write_text("\n".join(lines))
            res = noop.measure_transcripts([f], days=7)
            self.assertEqual(res['summary']['total_wakes'], 0)

    def test_cmd_noop_measure_cli(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "session.jsonl"
            f.write_text(SYNTHETIC_WAKE_SESSION)

            # Test text table output
            si_path = Path(__file__).resolve().parent.parent / 'si.py'
            out = subprocess.check_output(
                [sys.executable, str(si_path), "noop-measure", "--transcripts", str(f)],
                text=True,
                cwd=str(si_path.parent)
            )
            self.assertIn("No-op notification wake measurement", out)
            self.assertIn("background build finished", out)
            self.assertIn("66.7%", out)

            # Test --json output
            json_out = subprocess.check_output(
                [sys.executable, str(si_path), "noop-measure", "--transcripts", str(f), "--json"],
                text=True,
                cwd=str(si_path.parent)
            )
            data = json.loads(json_out)
            self.assertEqual(data['summary']['total_wakes'], 3)
            self.assertEqual(data['summary']['total_noop_wakes'], 2)


if __name__ == '__main__':
    unittest.main()
