import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import graph
import si

FIXTURE_GLOSSARY = Path(__file__).parent / 'fixtures' / 'glossary_fixture.md'


class TestKnowledgeGraph(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / 'graph.sqlite'
        self.records_path = self.root / 'records.jsonl'
        self.proposals_path = self.root / 'proposals.jsonl'
        self.advancements_path = self.root / 'advancements.jsonl'
        self.tested_models_path = self.root / 'tested_models.json'
        self.tokens_path = self.root / 'tokens.jsonl'
        self.glossary_path = self.root / 'concepts.md'

        # Write synthetic fixture glossary
        self.glossary_path.write_text(
            "# Concepts\n\n"
            "## Speculative Decoding (2026-10-20)\nDraft model token prediction.\n\n"
            "## Context Window (2026-10-15)\nToken attention buffer.\n\n"
            "## Speculative Decoding (2026-10-25)\nDuplicate term test.\n",
            encoding='utf-8'
        )

        # Write synthetic records
        records_data = [
            {'at': 1730000000, 'task': 'task-101', 'master': 'claude', 'labour': 'qwen', 'tool': 'qwen',
             'outcome': 'done', 'minutes': 3.5, 'blockers': []},
            {'at': 1730086400, 'task': 'task-102', 'master': 'claude', 'labour': 'agy', 'tool': 'qwen',
             'outcome': 'failed', 'minutes': 12.0, 'blockers': ['sandbox permission']},
        ]
        self.records_path.write_text('\n'.join(json.dumps(r) for r in records_data) + '\n')

        # Write synthetic proposals & decisions
        proposals_events = [
            {'at': 1730000000, 'source': 'agent', 'idea': 'Split large tasks', 'why': 'Avoid timeouts',
             'status': 'open', 'tool': 'orchestrator'},
            {'at': 1730010000, 'type': 'decision', 'proposal': 1, 'decision': 'adopted', 'note': 'Approved for trial'},
            {'at': 1730020000, 'source': 'scan:models', 'idea': 'Adopt model Qwen3-4B for coder labour',
             'why': 'New candidate', 'status': 'open', 'tool': 'Qwen3-4B'},
            {'at': 1730030000, 'type': 'decision', 'proposal': 2, 'decision': 'rejected', 'note': 'Declined by captain due to speed regression'},
        ]
        self.proposals_path.write_text('\n'.join(json.dumps(e) for e in proposals_events) + '\n')

        # Write synthetic advancements
        adv_data = [
            {'at': 1730000000, 'tool': 'hf-models', 'type': 'model_candidate', 'repo': 'Qwen/Qwen-7B-GGUF',
             'publisher': 'Qwen', 'params': 7e9, 'size_gb': 4.68, 'fits': True, 'best_candidate': True},
            {'at': 1730020000, 'tool': 'hf-models', 'type': 'model_candidate', 'repo': 'Qwen/Qwen3-4B-GGUF',
             'publisher': 'Qwen', 'params': 4e9, 'size_gb': 2.33, 'fits': True, 'verdict': 'declined'},
        ]
        self.advancements_path.write_text('\n'.join(json.dumps(a) for a in adv_data) + '\n')

        # Write synthetic tested_models.json with Qwen3-4B declined
        tested_data = {
            'Qwen/Qwen3-4B-GGUF': {
                'tested_at': '2026-10-30T10:00:00Z',
                'verdict': 'declined',
                'summary_file': '/tmp/bench-logs/qwen3_4b_summary.json'
            },
            'bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF': {
                'tested_at': '2026-10-29T12:00:00Z',
                'verdict': 'replace',
                'summary_file': '/tmp/bench-logs/qwen1.5b_summary.json'
            }
        }
        self.tested_models_path.write_text(json.dumps(tested_data, indent=2))

        # Write synthetic tokens
        tokens_data = [
            {'cli': 'claude', 'timestamp': '2026-10-30T12:00:00Z', 'total': 15000, 'model': 'claude-sonnet-4-6'},
            {'cli': 'qwen', 'timestamp': '2026-10-30T13:00:00Z', 'total': 4500, 'model': 'qwen2.5-coder-3b'},
        ]
        self.tokens_path.write_text('\n'.join(json.dumps(t) for t in tokens_data) + '\n')

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_schema_and_indexes(self):
        """Database is created with facts, concepts, and required indexes."""
        conn = graph.init_db(self.db_path, recreate=True)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        self.assertIn('facts', tables)
        self.assertIn('concepts', tables)

        cur.execute("PRAGMA table_info(facts)")
        cols = {row[1] for row in cur.fetchall()}
        self.assertEqual(cols, {'subject', 'relation', 'object', 'date', 'source', 'display'})

        cur.execute("PRAGMA table_info(concepts)")
        concept_cols = {row[1] for row in cur.fetchall()}
        self.assertEqual(concept_cols, {'term', 'first_taught'})

        cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cur.fetchall()}
        self.assertIn('idx_facts_subject', indexes)
        self.assertIn('idx_facts_relation', indexes)
        conn.close()

    def test_concepts_glossary_parsing(self):
        """Glossary parser extracts term and earliest date from ## Term (YYYY-MM-DD) headings."""
        concepts = graph.parse_concepts_glossary(self.glossary_path)
        self.assertIn('Speculative Decoding', concepts)
        self.assertIn('Context Window', concepts)
        # Duplicate preserves earliest date
        self.assertEqual(concepts['Speculative Decoding'], '2026-10-20')
        self.assertEqual(concepts['Context Window'], '2026-10-15')

    def test_build_graph_and_idempotence(self):
        """Graph rebuild from fixtures is fast, idempotent, and produces consistent fact counts."""
        t0 = time.perf_counter()
        res1 = graph.build_graph(
            db_path=self.db_path,
            concepts_path=self.glossary_path,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )
        elapsed1 = time.perf_counter() - t0
        self.assertLess(elapsed1, 0.5, "Rebuild must be well under a second")
        self.assertGreater(res1['facts'], 15)
        self.assertEqual(res1['concepts'], 2)

        # Second rebuild must produce identical results
        t1 = time.perf_counter()
        res2 = graph.build_graph(
            db_path=self.db_path,
            concepts_path=self.glossary_path,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )
        elapsed2 = time.perf_counter() - t1
        self.assertLess(elapsed2, 0.5)
        self.assertEqual(res1['facts'], res2['facts'])
        self.assertEqual(res1['concepts'], res2['concepts'])

        # Verify no duplicate rows exist in sqlite
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM facts")
        total_rows = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT subject || relation || object || date || source) FROM facts")
        distinct_rows = cur.fetchone()[0]
        self.assertEqual(total_rows, distinct_rows)
        self.assertEqual(total_rows, res1['facts'])
        conn.close()

    def test_query_subject_and_relation(self):
        """Query returns compact facts matching subject, optionally filtered by relation."""
        graph.build_graph(
            db_path=self.db_path,
            concepts_path=self.glossary_path,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )

        # Query all facts for task-101 (requires private=True since outcome/labour/master are private relations)
        facts_default = graph.query_graph('task-101', db_path=self.db_path)
        self.assertEqual(facts_default, [])

        with tempfile.TemporaryDirectory() as fm_dir:
            (Path(fm_dir) / 'AGENTS.md').write_text('# Agents\n', encoding='utf-8')
            with unittest.mock.patch.dict(os.environ, {'FM_HOME': fm_dir}):
                facts = graph.query_graph('task-101', db_path=self.db_path, private=True)
                relations = {f[1] for f in facts}
                self.assertIn('outcome', relations)
                self.assertIn('labour', relations)
                self.assertIn('master', relations)

                # Query filtered by relation with private=True
                outcomes = graph.query_graph('task-101', relation='outcome', db_path=self.db_path, private=True)
                self.assertEqual(len(outcomes), 1)
                self.assertEqual(outcomes[0][2], 'done')

                # Formatting compact single line
                line = graph.format_fact_line(outcomes[0])
                self.assertEqual(line, 'task-101 outcome done (2024-10-27, records.jsonl)')

        # Without private=True, relation='outcome' raises PermissionError
        with self.assertRaises(PermissionError):
            graph.query_graph('task-101', relation='outcome', db_path=self.db_path)

    def test_qwen3_4b_declined_cli_query(self):
        """CLI query `si.py graph query Qwen3-4B` answers that Qwen3-4B is declined from fixture build."""
        graph.build_graph(
            db_path=self.db_path,
            concepts_path=self.glossary_path,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )

        # Query via si.main CLI
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ret = si.main(['graph', 'query', 'Qwen3-4B', '--db', str(self.db_path)])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn('declined', out)
        self.assertIn('Qwen3-4B', out)

        # Query specifically for relation 'declined'
        buf_rel = io.StringIO()
        with contextlib.redirect_stdout(buf_rel):
            ret_rel = si.main(['graph', 'query', 'Qwen3-4B', '--relation', 'declined', '--db', str(self.db_path)])
        self.assertEqual(ret_rel, 0)
        out_rel = buf_rel.getvalue()
        self.assertIn('Qwen3-4B declined true', out_rel)

    def test_declined_model_skip_in_picker_and_review(self):
        """Review and pick_best_candidate consult graph and never propose a declined model."""
        graph.build_graph(
            db_path=self.db_path,
            concepts_path=self.glossary_path,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )

        candidates = [
            {'repo': 'Qwen/Qwen3-4B-GGUF', 'name': 'Qwen3-4B', 'fits': True, 'params': 4e9},
            {'repo': 'deepseek-ai/DeepSeek-Coder-1.3B-GGUF', 'name': 'DeepSeek-Coder-1.3B', 'fits': True, 'params': 1.3e9},
        ]

        # pick_best_candidate should skip declined Qwen3-4B and pick DeepSeek
        best = si.pick_best_candidate(candidates, tested_repos=set(), graph_path=self.db_path)
        self.assertIsNotNone(best)
        self.assertEqual(best['repo'], 'deepseek-ai/DeepSeek-Coder-1.3B-GGUF')

        # If only declined model is present, returns None
        only_declined = [{'repo': 'Qwen/Qwen3-4B-GGUF', 'name': 'Qwen3-4B', 'fits': True, 'params': 4e9}]
        self.assertIsNone(si.pick_best_candidate(only_declined, tested_repos=set(), graph_path=self.db_path))

    def test_review_summary_line_and_report(self):
        """Review prints one-line graph size summary and includes section in weekly report."""
        graph.build_graph(
            db_path=self.db_path,
            concepts_path=self.glossary_path,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )

        reports_dir = self.root / 'reports'
        reports_dir.mkdir(parents=True, exist_ok=True)

        buf = io.StringIO()
        with patch('si.REPORTS', reports_dir), \
             patch('si.GRAPH_DB', self.db_path), \
             patch('si.CONCEPTS', self.glossary_path), \
             patch('si.RECORDS', self.records_path), \
             patch('si.PROPOSALS', self.proposals_path), \
             patch('si.ADVANCEMENTS', self.advancements_path), \
             patch('si.TOKENS', self.tokens_path), \
             patch('si.tune'), \
             patch('si.notify_review_ready'), \
             patch('si.reset_weekly_token_records'), \
             patch('si.models_scan', return_value={'seen': [], 'best_candidate': None, 'hub_sources': [], 'tier2_hits': []}), \
             patch('si.token_report_lines', return_value=['## Weekly token consumption']), \
             contextlib.redirect_stdout(buf):
            args = type('Args', (), {'days': 7, 'concepts': None})()
            si.cmd_review(args)

        stdout_val = buf.getvalue()
        self.assertIn('Knowledge graph:', stdout_val)
        self.assertIn('facts', stdout_val)
        self.assertIn('concepts', stdout_val)

        # Check generated markdown report
        reports = list(reports_dir.glob('*.md'))
        self.assertEqual(len(reports), 1)
        report_content = reports[0].read_text(encoding='utf-8')
        self.assertIn('## Knowledge graph memory', report_content)
        self.assertIn('Knowledge graph:', report_content)

    def test_graph_build_cli_invocation(self):
        """CLI invocation `si.py graph build --concepts ... --db ...` builds database successfully."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ret = si.main(['graph', 'build', '--concepts', str(self.glossary_path), '--db', str(self.db_path)])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn('built graph:', out)
        self.assertIn('facts', out)
        self.assertIn('concepts', out)
        self.assertTrue(self.db_path.exists())

    def test_no_concepts_parameter_leaves_concepts_empty(self):
        """When --concepts is omitted, concepts table is empty and no external file is read."""
        res = graph.build_graph(
            db_path=self.db_path,
            concepts_path=None,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )
        self.assertEqual(res['concepts'], 0)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM concepts")
        self.assertEqual(cur.fetchone()[0], 0)
        conn.close()

    def test_missing_or_empty_subject_query(self):
        """Querying an unknown subject returns an empty list and prints nothing."""
        graph.build_graph(
            db_path=self.db_path,
            concepts_path=self.glossary_path,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )
        facts = graph.query_graph('NonExistentSubject123', db_path=self.db_path)
        self.assertEqual(facts, [])

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ret = si.main(['graph', 'query', 'NonExistentSubject123', '--db', str(self.db_path)])
        self.assertEqual(ret, 0)
        self.assertEqual(buf.getvalue(), '')

    def test_model_subject_aliases_variations(self):
        """Alias extraction correctly extracts full repo, short name, and un-suffixed base."""
        aliases = graph.model_subject_aliases('Qwen/Qwen3-4B-GGUF')
        self.assertEqual(aliases, ['Qwen/Qwen3-4B-GGUF', 'Qwen3-4B-GGUF', 'Qwen3-4B', 'Qwen/Qwen3-4B'])

        aliases_dot = graph.model_subject_aliases('bartowski/Llama-3.2-3B-Instruct.gguf')
        self.assertIn('bartowski/Llama-3.2-3B-Instruct.gguf', aliases_dot)
        self.assertIn('Llama-3.2-3B-Instruct.gguf', aliases_dot)
        self.assertIn('Llama-3.2-3B-Instruct', aliases_dot)

        self.assertEqual(graph.model_subject_aliases(''), [])
        self.assertEqual(graph.model_subject_aliases(None), [])

    def test_graph_build_cli_default_concepts_when_exists(self):
        """CLI invocation `si.py graph build` defaults --concepts to si.CONCEPTS when it exists."""
        buf = io.StringIO()
        with patch('si.CONCEPTS', self.glossary_path), contextlib.redirect_stdout(buf):
            ret = si.main(['graph', 'build', '--db', str(self.db_path)])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn('built graph:', out)
        self.assertIn('2 concepts', out)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM concepts")
        self.assertEqual(cur.fetchone()[0], 2)
        conn.close()

    def test_graph_build_cli_default_concepts_when_missing(self):
        """CLI invocation `si.py graph build` without --concepts handles missing default file gracefully (0 concepts)."""
        non_existent = self.root / 'missing_concepts.md'
        buf = io.StringIO()
        with patch('si.CONCEPTS', non_existent), contextlib.redirect_stdout(buf):
            ret = si.main(['graph', 'build', '--db', str(self.db_path)])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn('built graph:', out)
        self.assertIn('0 concepts', out)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM concepts")
        self.assertEqual(cur.fetchone()[0], 0)
        conn.close()

    def test_review_build_graph_default_concepts(self):
        """Weekly review graph build defaults to si.CONCEPTS when present, or 0 concepts when missing."""
        # When default exists
        with patch('si.CONCEPTS', self.glossary_path), \
             patch('si.GRAPH_DB', self.db_path), \
             patch('si.RECORDS', self.records_path), \
             patch('si.PROPOSALS', self.proposals_path), \
             patch('si.ADVANCEMENTS', self.advancements_path), \
             patch('si.TOKENS', self.tokens_path):
            res = si.review_build_graph()
            self.assertIsNotNone(res)
            self.assertEqual(res['concepts'], 2)

        # When default file is missing
        missing = self.root / 'nonexistent_concepts.md'
        with patch('si.CONCEPTS', missing), \
             patch('si.GRAPH_DB', self.db_path), \
             patch('si.RECORDS', self.records_path), \
             patch('si.PROPOSALS', self.proposals_path), \
             patch('si.ADVANCEMENTS', self.advancements_path), \
             patch('si.TOKENS', self.tokens_path):
            res = si.review_build_graph()
            self.assertIsNotNone(res)
            self.assertEqual(res['concepts'], 0)


    def test_case_duplicate_collapses_to_one_subject(self):
        """Case duplicate records collapse into one canonical subject in facts table."""
        # Setup tested_models with mixed cases: 'Qwen3-4B' and 'qwen3-4b'
        models_data = {
            'Qwen3-4B': {
                'tested_at': '2026-11-01T10:00:00Z',
                'verdict': 'fallback-only',
            },
            'qwen3-4b': {
                'tested_at': '2026-10-31T10:00:00Z',
                'verdict': 'fallback-only',
            }
        }
        test_path = self.root / 'case_models.json'
        test_path.write_text(json.dumps(models_data), encoding='utf-8')

        res = graph.build_graph(
            db_path=self.db_path,
            concepts_path=False,
            records_path=False,
            proposals_path=False,
            advancements_path=False,
            tested_models_path=test_path,
            tokens_path=False,
        )

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT subject FROM facts")
        subjects = [row[0] for row in cur.fetchall()]
        conn.close()

        # Both 'Qwen3-4B' and 'qwen3-4b' must collapse to canonical lowercase 'qwen3-4b'
        self.assertEqual(subjects, ['qwen3-4b'])

    def test_alias_resolves_at_build_and_query(self):
        """Aliases resolve to canonical name at build time and query time."""
        alias_file = self.root / 'test_aliases.json'
        alias_file.write_text(json.dumps({"qwen-legacy": "qwen3-4b"}), encoding='utf-8')

        models_data = {
            'Qwen-Legacy': {
                'tested_at': '2026-10-31T10:00:00Z',
                'verdict': 'fallback-only',
            }
        }
        test_path = self.root / 'alias_models.json'
        test_path.write_text(json.dumps(models_data), encoding='utf-8')

        graph.build_graph(
            db_path=self.db_path,
            concepts_path=False,
            records_path=False,
            proposals_path=False,
            advancements_path=False,
            tested_models_path=test_path,
            tokens_path=False,
            aliases_path=alias_file,
        )

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT subject, display FROM facts WHERE relation='tested'")
        row = cur.fetchone()
        conn.close()

        # Subject should be canonical 'qwen3-4b', display retains 'Qwen-Legacy'
        self.assertEqual(row[0], 'qwen3-4b')
        self.assertEqual(row[1], 'Qwen-Legacy')

        # Querying by alias 'qwen-legacy' case-insensitively resolves to 'qwen3-4b'
        facts = graph.query_graph('Qwen-Legacy', db_path=self.db_path, aliases_path=alias_file)
        self.assertTrue(len(facts) > 0)
        self.assertEqual(facts[0][0], 'qwen3-4b')

        # Querying by target 'QWEN3-4B' matches
        facts_target = graph.query_graph('QWEN3-4B', db_path=self.db_path, aliases_path=alias_file)
        self.assertEqual(len(facts), len(facts_target))

    def test_fallback_only_blocks_reproposal(self):
        """Model with verdict 'fallback-only' is treated as tried/declined and blocks re-proposal."""
        models_data = {
            'Qwen/Qwen3-4B-GGUF': {
                'tested_at': '2026-11-01T10:00:00Z',
                'verdict': 'fallback-only',
            }
        }
        test_path = self.root / 'fb_models.json'
        test_path.write_text(json.dumps(models_data), encoding='utf-8')

        graph.build_graph(
            db_path=self.db_path,
            concepts_path=False,
            records_path=False,
            proposals_path=False,
            advancements_path=False,
            tested_models_path=test_path,
            tokens_path=False,
        )

        declined = graph.get_declined_models(db_path=self.db_path)
        self.assertIn('qwen3-4b', declined)

        candidates = [
            {'repo': 'Qwen/Qwen3-4B-GGUF', 'name': 'Qwen3-4B', 'fits': True, 'params': 4e9},
            {'repo': 'deepseek-ai/DeepSeek-Coder-1.3B-GGUF', 'name': 'DeepSeek-Coder-1.3B', 'fits': True, 'params': 1.3e9},
        ]
        # pick_best_candidate must block Qwen3-4B because it is in declined
        best = si.pick_best_candidate(candidates, tested_repos=set(), graph_path=self.db_path)
        self.assertIsNotNone(best)
        self.assertEqual(best['repo'], 'deepseek-ai/DeepSeek-Coder-1.3B-GGUF')

        # If only Qwen3-4B candidate, pick_best_candidate returns None
        only_fb = [{'repo': 'Qwen/Qwen3-4B-GGUF', 'name': 'Qwen3-4B', 'fits': True, 'params': 4e9}]
        self.assertIsNone(si.pick_best_candidate(only_fb, tested_repos=set(), graph_path=self.db_path))

    def test_unknown_verdict_surfaced(self):
        """Unknown verdict word is reported in warnings, not silently dropped."""
        models_data = {
            'TestModel-1B': {
                'tested_at': '2026-11-01T10:00:00Z',
                'verdict': 'super-awesome-verdict',
            }
        }
        test_path = self.root / 'unknown_verdict_models.json'
        test_path.write_text(json.dumps(models_data), encoding='utf-8')

        res = graph.build_graph(
            db_path=self.db_path,
            concepts_path=False,
            records_path=False,
            proposals_path=False,
            advancements_path=False,
            tested_models_path=test_path,
            tokens_path=False,
        )

        self.assertTrue(any('unknown verdict word' in w for w in res.get('warnings', [])))
        self.assertTrue(any('super-awesome-verdict' in w for w in res.get('warnings', [])))

        # Also still stored in graph
        facts = graph.query_graph('testmodel-1b', relation='verdict', db_path=self.db_path)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0][2], 'super-awesome-verdict')

    def test_public_lock_enforcement(self):
        """Public queries succeed; private relations and concepts are refused without valid FM_HOME."""
        graph.build_graph(
            db_path=self.db_path,
            concepts_path=self.glossary_path,
            records_path=self.records_path,
            proposals_path=self.proposals_path,
            advancements_path=self.advancements_path,
            tested_models_path=self.tested_models_path,
            tokens_path=self.tokens_path,
        )

        # 1. Public query works (e.g. relation 'declined')
        facts = graph.query_graph('Qwen3-4B', relation='declined', db_path=self.db_path)
        self.assertTrue(any(f[1] == 'declined' for f in facts))

        # CLI public query works
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ret = si.main(['graph', 'query', 'Qwen3-4B', '--relation', 'declined', '--db', str(self.db_path)])
        self.assertEqual(ret, 0)
        self.assertIn('declined true', buf.getvalue())

        # 2. Private relation refused by default (Python API and CLI)
        with self.assertRaises(PermissionError) as cm:
            graph.query_graph('task-101', relation='outcome', db_path=self.db_path)
        self.assertIn('--private', str(cm.exception))

        buf_err = io.StringIO()
        with contextlib.redirect_stdout(buf_err):
            ret = si.main(['graph', 'query', 'task-101', '--relation', 'outcome', '--db', str(self.db_path)])
        self.assertEqual(ret, 1)
        self.assertIn('refused:', buf_err.getvalue())
        self.assertIn('--private', buf_err.getvalue())

        # 3. Refused with --private but no/invalid FM_HOME
        # 3a. FM_HOME not set
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError) as cm:
                graph.query_graph('task-101', relation='outcome', db_path=self.db_path, private=True)
            self.assertIn('FM_HOME', str(cm.exception))

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ret = si.main(['graph', 'query', 'task-101', '--relation', 'outcome', '--private', '--db', str(self.db_path)])
            self.assertEqual(ret, 1)
            self.assertIn('refused:', buf.getvalue())
            self.assertIn('FM_HOME', buf.getvalue())

        # 3b. FM_HOME points to nonexistent directory
        with unittest.mock.patch.dict(os.environ, {'FM_HOME': '/tmp/nonexistent_fm_home_xyz_123'}):
            with self.assertRaises(PermissionError) as cm:
                graph.query_graph('task-101', relation='outcome', db_path=self.db_path, private=True)
            self.assertIn('FM_HOME', str(cm.exception))

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ret = si.main(['graph', 'query', 'task-101', '--relation', 'outcome', '--private', '--db', str(self.db_path)])
            self.assertEqual(ret, 1)
            self.assertIn('refused:', buf.getvalue())

        # 3c. FM_HOME exists but does not contain AGENTS.md
        with tempfile.TemporaryDirectory() as empty_dir:
            with unittest.mock.patch.dict(os.environ, {'FM_HOME': empty_dir}):
                with self.assertRaises(PermissionError) as cm:
                    graph.query_graph('task-101', relation='outcome', db_path=self.db_path, private=True)
                self.assertIn('FM_HOME', str(cm.exception))

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ret = si.main(['graph', 'query', 'task-101', '--relation', 'outcome', '--private', '--db', str(self.db_path)])
                self.assertEqual(ret, 1)
                self.assertIn('refused:', buf.getvalue())

        # 4. Allowed with --private and a valid temp FM_HOME containing AGENTS.md
        with tempfile.TemporaryDirectory() as valid_fm_home:
            (Path(valid_fm_home) / 'AGENTS.md').write_text('# Agents\n', encoding='utf-8')
            with unittest.mock.patch.dict(os.environ, {'FM_HOME': valid_fm_home}):
                facts = graph.query_graph('task-101', relation='outcome', db_path=self.db_path, private=True)
                self.assertEqual(len(facts), 1)
                self.assertEqual(facts[0][2], 'done')

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    ret = si.main(['graph', 'query', 'task-101', '--relation', 'outcome', '--private', '--db', str(self.db_path)])
                self.assertEqual(ret, 0)
                self.assertIn('task-101 outcome done', buf.getvalue())

        # 5. Concepts lookups refused by default
        # Concepts are stored with relation 'taught_on' and 'status'='taught'
        with self.assertRaises(PermissionError):
            graph.query_graph('Speculative Decoding', relation='taught_on', db_path=self.db_path)

        # Unfiltered query on a concept subject returns only public relations by default (taught_on is filtered out)
        facts_concept = graph.query_graph('Speculative Decoding', db_path=self.db_path)
        for f in facts_concept:
            self.assertIn(f[1], graph.PUBLIC_RELATIONS)
            self.assertNotEqual(f[1], 'taught_on')

        # Allowed with --private and valid FM_HOME
        with tempfile.TemporaryDirectory() as valid_fm_home:
            (Path(valid_fm_home) / 'AGENTS.md').write_text('# Agents\n', encoding='utf-8')
            with unittest.mock.patch.dict(os.environ, {'FM_HOME': valid_fm_home}):
                facts_concept = graph.query_graph('Speculative Decoding', relation='taught_on', db_path=self.db_path, private=True)
                self.assertTrue(len(facts_concept) >= 1)
                self.assertEqual(facts_concept[0][1], 'taught_on')


if __name__ == '__main__':
    unittest.main()
