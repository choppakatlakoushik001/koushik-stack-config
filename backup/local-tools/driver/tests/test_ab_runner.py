"""Unit tests for the ab/run_ab.py test kit."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ab.run_ab as run_ab
from ab.tasks import TASKS


class ABKitTests(unittest.TestCase):
    def test_fixture_repo_initialization_and_tasks_count(self):
        # Spec asks for 8-12 synthetic tasks
        self.assertGreaterEqual(len(TASKS), 8)
        self.assertLessEqual(len(TASKS), 12)

        with tempfile.TemporaryDirectory() as d:
            repo_dir = Path(d) / "test_repo"
            repo_path, commit = run_ab.init_fixture_repo(repo_dir)
            self.assertEqual(repo_path, repo_dir)
            self.assertEqual(len(commit), 40)

            # Check that git status is clean
            res = subprocess.run(["git", "-C", str(repo_dir), "status", "--porcelain"], capture_output=True, text=True)
            self.assertEqual(res.stdout.strip(), "")

            # Verify files for all tasks are present
            for task in TASKS:
                self.assertTrue((repo_dir / task["file"]).is_file())
                self.assertTrue((repo_dir / task["test_file"]).is_file())

    def test_spec_creation_for_all_four_arms(self):
        with tempfile.TemporaryDirectory() as d:
            repo_dir = Path(d) / "test_repo"
            _, commit = run_ab.init_fixture_repo(repo_dir)

            task = TASKS[0]
            spec_base = run_ab.make_spec(task, "baseline", repo_dir, commit)
            self.assertEqual(spec_base["transport"], "diff")
            self.assertEqual(spec_base["typecheck_scope"], "file")

            spec_trans = run_ab.make_spec(task, "transport-only", repo_dir, commit)
            self.assertEqual(spec_trans["transport"], "function")
            self.assertEqual(spec_trans["typecheck_scope"], "file")

            spec_scope = run_ab.make_spec(task, "scope-only", repo_dir, commit)
            self.assertEqual(spec_scope["transport"], "diff")
            self.assertEqual(spec_scope["typecheck_scope"], "changed")

            spec_both = run_ab.make_spec(task, "both", repo_dir, commit)
            self.assertEqual(spec_both["transport"], "function")
            self.assertEqual(spec_both["typecheck_scope"], "changed")

    def test_compute_summary_metrics(self):
        completed = {
            "task_01:baseline": {
                "task": "task_01",
                "arm": "baseline",
                "verdict": "pass",
                "failure_kind": "none",
                "seconds": 10.0,
            },
            "task_02:baseline": {
                "task": "task_02",
                "arm": "baseline",
                "verdict": "fail",
                "failure_kind": "malformed_patch",
                "seconds": 5.0,
            },
            "task_01:transport-only": {
                "task": "task_01",
                "arm": "transport-only",
                "verdict": "pass",
                "failure_kind": "none",
                "seconds": 8.0,
            },
            "task_01:scope-only": {
                "task": "task_01",
                "arm": "scope-only",
                "verdict": "pass",
                "failure_kind": "none",
                "seconds": 9.0,
            },
            "task_01:both": {
                "task": "task_01",
                "arm": "both",
                "verdict": "pass",
                "failure_kind": "none",
                "seconds": 7.0,
            },
        }

        summary = run_ab.compute_summary(completed)
        self.assertIn("baseline", summary)
        self.assertIn("transport-only", summary)
        self.assertIn("scope-only", summary)
        self.assertIn("both", summary)

        self.assertEqual(summary["baseline"]["total_jobs"], 2)
        self.assertEqual(summary["baseline"]["passed_jobs"], 1)
        self.assertEqual(summary["baseline"]["pass_rate"], 0.5)
        self.assertEqual(summary["baseline"]["failure_kind_counts"], {"none": 1, "malformed_patch": 1})
        self.assertEqual(summary["baseline"]["total_seconds"], 15.0)

        self.assertEqual(summary["transport-only"]["total_jobs"], 1)
        self.assertEqual(summary["transport-only"]["passed_jobs"], 1)
        self.assertEqual(summary["transport-only"]["pass_rate"], 1.0)

        self.assertEqual(summary["scope-only"]["total_jobs"], 1)
        self.assertEqual(summary["scope-only"]["passed_jobs"], 1)
        self.assertEqual(summary["scope-only"]["pass_rate"], 1.0)

        self.assertEqual(summary["both"]["total_jobs"], 1)
        self.assertEqual(summary["both"]["passed_jobs"], 1)
        self.assertEqual(summary["both"]["pass_rate"], 1.0)
        self.assertEqual(summary["both"]["failure_kind_counts"], {"none": 1})
        self.assertEqual(summary["both"]["total_seconds"], 7.0)

    def test_jp_gate_busy_stops_cleanly_and_persists_state(self):
        with tempfile.TemporaryDirectory() as d:
            res_dir = Path(d) / "results"
            state_file = res_dir / "ab_state.json"

            with patch("ab.run_ab.check_jp_gate", return_value="busy: training running"):
                ret = run_ab.main(["--results", str(res_dir), "--state", str(state_file)])
                self.assertEqual(ret, 0)
                self.assertTrue(state_file.exists())
                state = json.loads(state_file.read_text())
                self.assertEqual(len(state["completed"]), 0)


if __name__ == "__main__":
    unittest.main()
