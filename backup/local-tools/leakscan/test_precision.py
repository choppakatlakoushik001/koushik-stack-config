"""Precision regressions: real shell lines that must not be flagged (synthetic secrets still are)."""
import unittest

import leakscan

CLEAN = [
    '    if [ "${WATCHER_RECOVERY_PENDING:-0}" -eq 1 ] \\',
    """printf '%s\\n' "$WATCH_PATH" > "$WATCH_LOCK/watcher-path" || true""",
    '      "$SCRIPT_DIR/fm-home-summary-refresh.sh" --best-effort </dev/null >/dev/null 2>&1 &',
    '  dir=$(make_primary_dir "$TMP_ROOT/hook-joined-fresh-shared-beacon")',
    '  touch -t 202001010000 "$dir/state/.last-watcher-beat"',
    'echo "deadline 1793258743 reached"',
    '- `tests/test_governor_v5.py::test_v5_budget_and_owner_activity_wait`',
]
DIRTY_TOKEN = 'token aZ9xQ2mL7pR4tY8wK3nB6vC1dF5 here'
DIRTY_PHONE = 'call +1 415-555-0134 now'


def rules(line):
    return [f.rule for f in leakscan.scan_line(line, 'bin/x.sh', 1, 'diff')]


class PrecisionTests(unittest.TestCase):
    def test_shell_lines_clean(self):
        for line in CLEAN:
            self.assertEqual(rules(line), [], line)

    def test_random_token_still_flagged(self):
        self.assertIn('high-entropy-token', rules(DIRTY_TOKEN))

    def test_formatted_phone_still_flagged(self):
        self.assertIn('phone-number', rules(DIRTY_PHONE))


if __name__ == '__main__':
    unittest.main()
