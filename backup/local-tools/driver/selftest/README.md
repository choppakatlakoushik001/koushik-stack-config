# Self-test fixtures

`pass/` and `fail/` are clean synthetic Git repositories. Run their adjacent specs with `qwen-task`; result records go to the driver's results directory. `check_safety.py` exercises path traversal and out-of-allowlist rejection without starting the model. The fail fixture's `choose(4)` test expects 9 while the task asks Qwen to return 8, verifying test-failure reporting.

`python3 -m unittest discover -s tests -v` at the repository root exercises the
new type-check gate, retry feedback, flags, and per-job metadata without calling
Qwen. Pyright or mypy is needed for the real-checker cases. The task-driver
integration cases read the fixed external pass fixture without changing it.

`python3 selftest/measure_ab.py` runs both fixed specs and three synthetic
function tasks against real Qwen with paired seeds 0 and 1. Output is under
`results/ab`; use a fresh `--results` directory to repeat. Synthetic inputs keep
the same existing annotations in both modes. The comparison is checking plus
annotation prompting (explicit `--typecheck`) versus the default-off
`--no-typecheck` baseline, rather than removing
types from source text. The fixed fail spec remains an intentional failure.
