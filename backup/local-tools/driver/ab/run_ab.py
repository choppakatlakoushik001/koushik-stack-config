#!/usr/bin/env python3
"""A/B test harness comparing four arms: baseline, transport-only, scope-only, and both.

Checks `jp-gate` before every job and stops cleanly with a resumable state file
if `jp-gate` prints anything other than `idle`.
Firstmate runs the real evaluation while the captain is idle.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import driver_support
from ab.tasks import TASKS, TaskDef

ARMS = {
    "baseline": {
        "transport": "diff",
        "typecheck_scope": "file",
    },
    "transport-only": {
        "transport": "function",
        "typecheck_scope": "file",
    },
    "scope-only": {
        "transport": "diff",
        "typecheck_scope": "changed",
    },
    "both": {
        "transport": "function",
        "typecheck_scope": "changed",
    },
}


def check_jp_gate() -> str:
    """Query jp-gate to check system idle status."""
    executable = shutil.which("jp-gate")
    if not executable:
        # If jp-gate is not found on path, assume idle or mock environment
        return "idle"
    try:
        proc = subprocess.run([executable], capture_output=True, text=True, timeout=10)
        return proc.stdout.strip()
    except Exception as exc:
        return f"error: {exc}"


def init_fixture_repo(target_dir: Path) -> tuple[Path, str]:
    """Initialize a clean Git fixture repository with all task files."""
    target_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(target_dir)], check=True)
    subprocess.run(["git", "-C", str(target_dir), "config", "user.name", "AB Harness"], check=True)
    subprocess.run(["git", "-C", str(target_dir), "config", "user.email", "harness"], check=True)

    # Populate task files and tests
    for task in TASKS:
        prod_path = target_dir / task["file"]
        prod_path.parent.mkdir(parents=True, exist_ok=True)
        prod_path.write_text(task["initial_code"], encoding="utf-8")

        test_path = target_dir / task["test_file"]
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(task["test_code"], encoding="utf-8")

    # Add all and commit
    subprocess.run(["git", "-C", str(target_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(target_dir), "commit", "-m", "Initial fixture commit", "-q"], check=True)

    head = subprocess.run(
        ["git", "-C", str(target_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return target_dir, head


def make_spec(task: TaskDef, arm_name: str, repo_path: Path, base_commit: str) -> dict[str, object]:
    """Build task spec JSON for a specific task and arm."""
    arm_config = ARMS[arm_name]
    prod_lines = len(task["initial_code"].splitlines())
    test_lines = len(task["test_code"].splitlines())

    return {
        "schema_version": 1,
        "repo": str(repo_path.resolve()),
        "base_commit": base_commit,
        "allowed_paths": [task["file"]],
        "line_ranges": {
            task["file"]: [{"start": 1, "end": prod_lines}],
            task["test_file"]: [{"start": 1, "end": test_lines}],
        },
        "test_command": [
            "python3",
            "-m",
            "unittest",
            "-q",
            task["test_module"],
        ],
        "acceptance": task["acceptance"],
        "token_estimate": task["token_estimate"],
        "transport": arm_config["transport"],
        "typecheck_scope": arm_config["typecheck_scope"],
    }


def compute_summary(completed: dict[str, dict[str, object]]) -> dict[str, object]:
    """Compute aggregate pass rates, failure kind counts, and timings per arm."""
    summary: dict[str, object] = {}
    for arm_name in ARMS:
        arm_jobs = [j for j in completed.values() if j.get("arm") == arm_name]
        total = len(arm_jobs)
        passed = sum(1 for j in arm_jobs if j.get("verdict") == "pass")
        seconds = [float(j.get("seconds", 0.0)) for j in arm_jobs]
        total_sec = round(sum(seconds), 3)
        mean_sec = round(total_sec / total, 3) if total > 0 else 0.0

        kind_counts: dict[str, int] = {}
        for j in arm_jobs:
            k = str(j.get("failure_kind", "unknown"))
            kind_counts[k] = kind_counts.get(k, 0) + 1

        summary[arm_name] = {
            "total_jobs": total,
            "passed_jobs": passed,
            "pass_rate": round(passed / total, 3) if total > 0 else 0.0,
            "failure_kind_counts": kind_counts,
            "total_seconds": total_sec,
            "mean_seconds_per_job": mean_sec,
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=ROOT / "ab/results", help="Directory for A/B results")
    ap.add_argument("--state", type=Path, default=None, help="Resumable state JSON path")
    ap.add_argument("--smoke", action="store_true", help="Run up to 2 jobs as a smoke test")
    ap.add_argument("--max-jobs", type=int, default=None, help="Maximum number of jobs to run before stopping")
    ap.add_argument("--seed", type=int, default=0, help="Model seed")
    args = ap.parse_args(argv)

    results_dir = args.results.resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    state_file = args.state.resolve() if args.state else results_dir / "ab_state.json"
    summary_file = results_dir / "ab_summary.json"

    # Load resumable state
    state: dict[str, object] = {"completed": {}, "summary": {}}
    if state_file.is_file():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and "completed" in loaded:
                state = loaded
                print(f"Loaded existing state with {len(state['completed'])} completed jobs.")
        except Exception as exc:
            print(f"Warning: could not read existing state file: {exc}", file=sys.stderr)

    completed: dict[str, dict[str, object]] = state["completed"]  # type: ignore

    # Set up synthetic fixture repo
    repo_dir = results_dir / "fixture_repo"
    repo_path, base_commit = init_fixture_repo(repo_dir)

    task_list = TASKS
    if args.smoke:
        # Smoke test runs at most 2 jobs
        max_jobs = 2
    else:
        max_jobs = args.max_jobs

    jobs_run_this_session = 0

    print("=== Starting A/B Evaluation Suite ===")
    print(f"Fixture repo: {repo_path} (commit: {base_commit[:8]})")
    print(f"Results dir:  {results_dir}")
    print(f"State file:   {state_file}")

    for arm_name in ARMS:
        for task in task_list:
            key = f"{task['name']}:{arm_name}"
            if key in completed:
                continue

            if max_jobs is not None and jobs_run_this_session >= max_jobs:
                print(f"Reached limit of {max_jobs} jobs for this session.")
                break

            if args.smoke:
                arm_completed = sum(1 for j in completed.values() if j.get("arm") == arm_name)
                if arm_completed >= 1:
                    break

            # 1. Check jp-gate before every job
            gate_status = check_jp_gate()
            if gate_status != "idle":
                print(f"jp-gate is busy ({gate_status}); stopping cleanly.", file=sys.stderr)
                state["summary"] = compute_summary(completed)
                state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
                print(f"State saved to {state_file}.")
                return 0

            # 2. Prepare task spec
            spec_data = make_spec(task, arm_name, repo_path, base_commit)
            spec_dir = results_dir / "specs" / arm_name
            spec_dir.mkdir(parents=True, exist_ok=True)
            spec_path = spec_dir / f"{task['name']}.json"
            spec_path.write_text(json.dumps(spec_data, indent=2) + "\n", encoding="utf-8")

            # 3. Execute qwen_task
            job_results_dir = results_dir / "jobs" / arm_name / task["name"]
            job_results_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                sys.executable,
                str(ROOT / "qwen_task.py"),
                str(spec_path),
                "--typecheck",
                "--seed",
                str(args.seed),
                "--results",
                str(job_results_dir),
            ]

            print(f"Running job: {task['name']} [{arm_name}] ...", flush=True)
            t_start = time.monotonic()
            proc = subprocess.run(cmd, capture_output=True, text=True)
            elapsed = round(time.monotonic() - t_start, 3)

            # 4. Read result verdict
            verdict_files = list(job_results_dir.glob("*/verdict.json"))
            if verdict_files:
                verdict_data = json.loads(verdict_files[-1].read_text(encoding="utf-8"))
                outcome = verdict_data.get("verdict", "fail")
                failure_kind = driver_support.get_failure_kind(verdict_data)
                result_dir_str = str(verdict_files[-1].parent)
            else:
                outcome = "fail"
                failure_kind = "refused_spec" if proc.returncode != 0 else "unknown"
                result_dir_str = str(job_results_dir)

            record: dict[str, object] = {
                "task": task["name"],
                "arm": arm_name,
                "is_legacy": task["is_legacy"],
                "verdict": outcome,
                "failure_kind": failure_kind,
                "seconds": elapsed,
                "result_dir": result_dir_str,
            }
            completed[key] = record
            jobs_run_this_session += 1

            print(f"  -> {outcome} (failure_kind: {failure_kind}, {elapsed}s)")

            # Save state after every job
            state["summary"] = compute_summary(completed)
            state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

        if max_jobs is not None and jobs_run_this_session >= max_jobs:
            break

    # Final summary output
    summary = compute_summary(completed)
    state["summary"] = summary
    state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("\n=== A/B Summary ===")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
