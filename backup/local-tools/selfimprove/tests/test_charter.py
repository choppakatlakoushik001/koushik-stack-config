import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import si


class TestCharter(unittest.TestCase):
    def test_repo_charter_has_valid_statuses(self):
        """The repository CHARTER.md must parse cleanly and every row must have a valid status."""
        rows = si.charter_rows(si.CHARTER)
        self.assertGreaterEqual(len(rows), 12)
        valid = set(si.VALID_CHARTER_STATUSES)
        for r in rows:
            self.assertIn(r['base_status'], valid, f"Invalid status for {r['id']}: {r['status']}")

    def test_repo_charter_open_rows(self):
        """charter_open_rows returns all non-built rows from the repo charter."""
        open_rows = si.charter_open_rows(si.CHARTER)
        open_ids = [r['id'] for r in open_rows]
        # Open rows are exactly the non-built ones; built rows never appear
        for r in open_rows:
            self.assertNotEqual(r['base_status'], 'built')
        self.assertNotIn('R1', open_ids)
        self.assertNotIn('R2', open_ids)
        self.assertNotIn('R3', open_ids)
        self.assertNotIn('R4', open_ids)
        self.assertNotIn('R8', open_ids)
        self.assertNotIn('R10', open_ids)
        self.assertNotIn('R12', open_ids)
        self.assertNotIn('R13', open_ids)
        self.assertNotIn('R14', open_ids)
        self.assertNotIn('R15', open_ids)
        self.assertNotIn('R16', open_ids)
        self.assertNotIn('R17', open_ids)
        self.assertNotIn('R18', open_ids)
        self.assertNotIn('R19', open_ids)

    def test_invalid_status_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / 'CHARTER.md'
            p.write_text(
                "# Charter\n\n## Requirements and status\n\n"
                "| # | Requirement | Status | Where |\n"
                "|---|---|---|---|\n"
                "| R1 | Valid item | built | code |\n"
                "| R2 | Bad item | unknown_status | nowhere |\n"
            )
            with self.assertRaises(ValueError) as ctx:
                si.charter_rows(p)
            self.assertIn('invalid charter status', str(ctx.exception))

    def test_all_valid_status_types_accepted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / 'CHARTER.md'
            p.write_text(
                "# Charter\n\n## Requirements and status\n\n"
                "| # | Requirement | Status | Where |\n"
                "|---|---|---|---|\n"
                "| R1 | Done item | built | file |\n"
                "| R2 | In-progress item | built-not-wired | file |\n"
                "| R3 | Proposed item | proposed #99 | |\n"
                "| R4 | Gap item | gap (needs work) | |\n"
            )
            rows = si.charter_rows(p)
            self.assertEqual(len(rows), 4)
            open_rows = si.charter_open_rows(p)
            self.assertEqual([r['id'] for r in open_rows], ['R2', 'R3', 'R4'])

    def test_review_includes_charter_gaps_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            charter_path = root / 'CHARTER.md'
            charter_path.write_text(
                "# Charter\n\n## Requirements and status\n\n"
                "| # | Requirement | Status | Where |\n"
                "|---|---|---|---|\n"
                "| R1 | One | built | here |\n"
                "| R2 | Two | proposed #42 | later |\n"
            )
            lines = si.charter_gap_lines(charter_path)
            rendered = '\n'.join(lines)
            self.assertIn('## Charter gaps', rendered)
            self.assertIn('- R2: Two [proposed #42] (later)', rendered)
            self.assertNotIn('R1: One', rendered)


if __name__ == '__main__':
    unittest.main()
