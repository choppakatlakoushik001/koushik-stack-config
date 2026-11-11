# Local Qwen task driver

`qwen-task SPEC.json` runs one code-only task against a clean Git commit. It creates a timestamped result directory under `~/local-llm/driver/results/`; nothing from the generated patch is copied back to the source repository.

## Use

1. Create a strict JSON spec (schema version 1), using a full commit hash and an absolute repository path.
2. List every file Qwen may change in `allowed_paths`.
3. Give line ranges for each allowed file and any test/source context in `line_ranges`. Context-only paths are allowed in `line_ranges` but must not appear in `allowed_paths`.
4. Use a named pytest or unittest command in the repository's test tree. Only a small offline command subset is accepted.
5. Run `qwen-task /absolute/path/to/task.json`.

Example:

```json
{
  "schema_version": 1,
  "repo": "/work/project",
  "base_commit": "0123456789abcdef0123456789abcdef01234567",
  "allowed_paths": ["src/math.py"],
  "line_ranges": {
    "src/math.py": [{"start": 1, "end": 20}],
    "tests/test_math.py": [{"start": 1, "end": 40}]
  },
  "test_command": ["python3", "-m", "pytest", "tests/test_math.py", "-q"],
  "acceptance": "Fix the stated arithmetic regression without changing test expectations.",
  "token_estimate": 900
}
```

The driver refuses dirty repositories, a nonmatching base commit, unsafe paths, invalid schemas, out-of-allowlist patches, binary/create/delete/rename/mode changes, invalid patch application, and prompts above 3,000 server-tokenized tokens. The declared estimate must also cover the actual prompt. Generation is capped at 1,200 tokens. The model sees only the acceptance text, test command, allowlist, and requested source/test line ranges. Tests run in a temporary archive copy with temporary `HOME`, `TMPDIR`, and XDG config/data directories. The driver records a result even when it refuses a task.

Each result folder contains `spec.json`, `prompt.txt` (the actual prompt), `model_reply.txt`, `patch.diff` (only after path validation), `test_output.txt`, and `verdict.json` with start/finish timestamps, base commit, spec hash, changed paths, test exit code, measured model time, and whether this invocation started the server. A failing verdict and patch are for review; no patch is applied to the source repository.

## Self-test

Run `python3 ~/local-llm/driver/selftest/check_safety.py` for local allowlist/path rejection checks. The `selftest/pass` and `selftest/fail` repositories are tiny disposable fixtures with specs alongside them. Their measured real-model records are described in the firstmate task report. The fail fixture intentionally expects 9 while its task acceptance requests 8, so its test should fail after Qwen's change.

## Limits

This is a small local harness, not a security sandbox. It limits test commands and gives tests temporary data/config roots, but this host does not permit `unshare -n`; a test that deliberately opens a network connection is not blocked by an OS network namespace. Use it only with trusted test suites explicitly intended to run offline. Qwen may emit malformed or semantically wrong patches; malformed output is refused and test failures remain failures. Hunk counts may be normalized, but `git apply --check` must still accept the actual context. A passing test suite is not a code review and never auto-lands a patch.

## Type checks and function filling

Static type checking is **off by default** in both drivers. Pass `--typecheck` to
request explicit parameter and return annotations and require strict static type
checking as well as passing tests. Per-job size and outcome logging is always on.
When checking is enabled, the driver uses the first available `pyright` or `mypy` on `PATH`, with its own configuration. Nothing is
installed automatically. If checking is enabled, a missing checker fails before
generation with an actionable message. Strict checking covers each entire changed `.py` file, including existing
code; it can therefore reject pre-existing typing problems. Non-Python changes
have no Python files to check.

`qwen_task.py` preserves its one-generation budget. `fill.py` preserves its default
three-attempt budget: test failures, parse failures, and enabled checker failures each
consume an attempt. Tests and the enabled checker run on the scratch copy; a passing
test suite alone cannot accept an enabled checker failure. The next prompt includes the
previous function, test output, and enabled checker diagnostics. Only a candidate
passing all enabled checks is written to the requested source file.

```sh
qwen-server start
python3 fill.py /work/repo/calc.py add_one /work/repo/tests/test_calc.py tests.test_calc --typecheck --results ./results
qwen-server stop
python3 qwen_task.py /absolute/path/to/task.json --no-typecheck --seed 0 --results ./results
```

`fill.py` requires an existing local server; `qwen_task.py` starts one if needed
and stops only the instance it started. Both accept `--typecheck` to opt in. With
no checking flag, neither the annotation request nor the checker runs. The compatibility flag `--no-typecheck` explicitly
selects that same default baseline. Existing input annotations are preserved.
Both accept `--results DIR` and `--seed N`.
The fill seed defaults to zero and increments on each retry, retaining the previous
temperature rule; the task driver sends a seed only when explicitly supplied.
Model, temperature, output limits, and server runtime settings are otherwise unchanged.

Each `verdict.json` now includes a `jobs` list with one record per generation:
`prompt_tokens`, `output_lines`, `verdict`, seed, model seconds, and test/checker
exit codes. Parse and patch failures retain their size measurements. Prompt tokens
are the server-tokenized user prompt, including retry feedback, excluding chat
wrappers and the task driver's system message. Output lines count the full raw
reply, including fences and the requested `REASON:` explanation. A null exit code
means a check did not run; `typecheck_enabled` distinguishes disabled checking.
`checker_caught_test_miss` means tests passed but the checker failed; inspect the
diagnostics before counting that as a semantic bug. Fill results also retain each
attempt's prompt, raw reply, test output, and checker output in `attempt-N/`.
Fill retains its existing external self-improvement summary event. Use
`--no-selfimprove` for isolated measurements to suppress that legacy event; detailed
result metadata is always written. The A/B runner suppresses external summary
events so synthetic experiments do not change the shared weekly ledger.

A type checker follows possible values through source code and compares them with
declared types without executing the program. It catches incompatible return types,
wrong argument types, and some missing returns on untested branches. It cannot
prove the requested arithmetic, completeness of tests, or correctness of annotations.
It is an additional review signal, not a sandbox or automatic approval to land code.

Run the model-free regression tests with `python3 -m unittest discover -s tests -v`
and the original safety checks with `python3 selftest/check_safety.py`.
Run the real-model comparison with `python3 selftest/measure_ab.py`; it reads the
fixed specs at `/home/chopp/local-llm/driver/selftest` without changing them, creates
three synthetic function fixtures under this worktree's `results/ab`, and explicitly
selects both `--typecheck` and `--no-typecheck` modes with seeds 0 and 1. Use a fresh
`--results DIR` for each repeat. It starts and
stops Qwen around the batch. Results include pass rate, attempts, wall time, size,
and checker failures that tests missed. See `TYPED_CHECK_REPORT.md` for the measured
sample and its limits.
