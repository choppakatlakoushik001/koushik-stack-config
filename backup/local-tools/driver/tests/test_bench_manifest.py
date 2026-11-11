"""Tests for tools/bench_manifest.py."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/bench_manifest.py"
sys.path.insert(0, str(ROOT))
import tools.bench_manifest as bench_manifest


class BenchManifestTests(unittest.TestCase):
    def test_refuses_to_write_inside_logdir(self):
        with tempfile.TemporaryDirectory() as d:
            logdir = Path(d) / "logs"
            logdir.mkdir()
            (logdir / "test.txt").write_text("hello")

            # Try writing directly inside logdir
            inside_out = logdir / "manifest.json"
            ret = bench_manifest.main([str(logdir), "--out", str(inside_out)])
            self.assertEqual(ret, 1)
            self.assertFalse(inside_out.exists())

            # Try writing in a nested subdirectory inside logdir
            nested_out = logdir / "nested" / "sub" / "manifest.json"
            ret = bench_manifest.main([str(logdir), "--out", str(nested_out)])
            self.assertEqual(ret, 1)
            self.assertFalse(nested_out.exists())

            # Try writing to logdir itself
            ret = bench_manifest.main([str(logdir), "--out", str(logdir)])
            self.assertEqual(ret, 1)

    def test_reproduces_sizes_hashes_and_fields_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            logdir = Path(d) / "bench_logs"
            logdir.mkdir()
            job_dir = logdir / "20261101-run1"
            job_dir.mkdir()

            prompt_content = "Please write a function add(a, b)"
            reply_content = "def add(a, b):\n    return a + b\nREASON: sum"
            test_content = "tests passed exit_code=0"
            verdict_data = {
                "verdict": "pass",
                "failure_kind": "none",
                "prompt_tokens": 12,
                "completion_tokens": 20,
                "model_seconds": 1.25,
                "output_lines": 3,
                "reason": "sum",
                "test_exit_code": 0,
            }

            (job_dir / "prompt.txt").write_text(prompt_content)
            (job_dir / "model_reply.txt").write_text(reply_content)
            (job_dir / "test_output.txt").write_text(test_content)
            (job_dir / "verdict.json").write_text(json.dumps(verdict_data, indent=2))

            out_file = Path(d) / "output" / "manifest.json"
            ret = bench_manifest.main([str(logdir), "--out", str(out_file)])
            self.assertEqual(ret, 0)
            self.assertTrue(out_file.exists())

            manifest = json.loads(out_file.read_text())
            self.assertEqual(manifest["file_count"], 4)

            records_by_path = {r["path"]: r for r in manifest["records"]}
            
            prompt_rec = records_by_path["20261101-run1/prompt.txt"]
            self.assertEqual(prompt_rec["size"], len(prompt_content.encode("utf-8")))
            self.assertEqual(prompt_rec["sha256"], hashlib.sha256(prompt_content.encode("utf-8")).hexdigest())
            self.assertEqual(prompt_rec["fields"], ["prompt_tokens"])

            reply_rec = records_by_path["20261101-run1/model_reply.txt"]
            self.assertEqual(reply_rec["size"], len(reply_content.encode("utf-8")))
            self.assertEqual(reply_rec["sha256"], hashlib.sha256(reply_content.encode("utf-8")).hexdigest())
            self.assertIn("completion_tokens", reply_rec["fields"])
            self.assertIn("model_seconds", reply_rec["fields"])
            self.assertIn("output_lines", reply_rec["fields"])
            self.assertIn("reason", reply_rec["fields"])

            test_rec = records_by_path["20261101-run1/test_output.txt"]
            self.assertIn("test_exit_code", test_rec["fields"])
            self.assertIn("verdict", test_rec["fields"])
            self.assertIn("failure_kind", test_rec["fields"])

    def test_logdir_is_strictly_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            logdir = Path(d) / "logs"
            logdir.mkdir()
            file_a = logdir / "a.txt"
            file_a.write_text("immutable content")
            mtime_before = file_a.stat().st_mtime_ns

            out_file = Path(d) / "manifest.json"
            bench_manifest.main([str(logdir), "--out", str(out_file)])

            # Verify file exists, size and mtime are unchanged
            self.assertTrue(file_a.exists())
            self.assertEqual(file_a.read_text(), "immutable content")
            self.assertEqual(file_a.stat().st_mtime_ns, mtime_before)
            # Verify no extra files created in logdir
            self.assertEqual(list(logdir.iterdir()), [file_a])


if __name__ == "__main__":
    unittest.main()
