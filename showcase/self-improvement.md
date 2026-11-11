# Self-improvement loop

I use a weekly review loop to turn measurements and ideas into small, reversible changes. It records task outcomes, keeps append-only proposal and decision histories, checks the tools I depend on for relevant updates, and produces a weekly review against eight goals: advanced, reliable, trustworthy, adaptable, safe, secure, efficient and conservative, and better throughput and experience.

## Three levels of authority

The loop separates observation from change:

1. Tier 0 measures and reports automatically.
2. Tier 1 can change one explicitly approved integer setting inside fixed bounds, one step at a time.
3. Tier 2 still needs an explicit decision from me for new tools, installs, source changes, deletion rules, new data access, or safety changes.

Only one Tier 1 trial can be active at a time. A trial records its intended change before applying it, uses atomic file replacement, and restores the old value after an interrupted application. An unexpected value from another writer stops the controller instead of being overwritten.

## Frozen comparisons

The included Tier 1 benchmark definition contains 30 synthetic function cases and 90 assertions. The manifest pins the case definitions, tests, scorer, seeds, and protocol with a content hash. Changing or removing any pinned input voids the comparison and causes an active trial to roll back.

A trial starts only after all required baseline cases have measurements. It runs for at least seven days and keeps a change only when the target strictly improves and the guard metric stays equal or improves. A tied target or a worse guard rolls back. Repeated runs of one case cannot inflate the sample count.

Every kept result also gets a reverse check: the previous value is measured again on the same frozen set. If the apparent gain does not disappear under the old value, the result is treated as unattributed and rolled back. This helps separate a real setting improvement from changing machine load or another environmental difference.

## Weekly coverage

The review covers more than tuning. It maintains an eight-goal scorecard, reports charter gaps, follows proposal decisions and retention, checks local token trends, surveys background applications, scans selected upstream sources for relevant changes, and watches for coder models that fit the machine's size limit. Heavy model comparisons stay idle-gated.

The knowledge graph stores relationships among public model facts, previous decisions, and measured outcomes. Exact matches come first; vector similarity is an additive hint for differently worded ideas and never blocks a proposal. Public model queries are separated from private setup history.

## Data boundary

The exported snapshot includes the loop's source, tests, synthetic fixtures, and frozen benchmark definition. It excludes the live graph database, proposal and outcome journals, token records, reports, model files, and generated results. Those runtime records stay local and are not needed to understand or restore the implementation.
