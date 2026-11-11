"""Unit tests for knowledge graph vector search with fake embedder and real model check."""
import contextlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import graph
import si


class TestVectorSearch(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.db_path = self.root / 'graph.sqlite'
        self.tunables_path = self.root / 'tunables.json'
        self.proposals_path = self.root / 'proposals.jsonl'
        self.hints_path = self.root / 'hints.jsonl'
        self.tested_models_path = self.root / 'tested_models.json'

        # Initialize test graph db
        graph.init_db(self.db_path, recreate=True)

        # Write test tunables.json
        self.tunables_path.write_text(json.dumps({
            "version": 1,
            "similarity_threshold": 0.65,
            "vector_similarity_threshold": 0.65,
            "tunables": []
        }))

    def test_cosine_similarity_math(self):
        """First principles cosine similarity math: dot(a,b) / (|a| * |b|)."""
        # Identical vectors -> 1.0
        v1 = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(graph.cosine_similarity(v1, v1), 1.0)

        # Orthogonal vectors -> 0.0
        v_x = [1.0, 0.0, 0.0]
        v_y = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(graph.cosine_similarity(v_x, v_y), 0.0)

        # Opposite vectors -> -1.0
        v_neg = [-1.0, -2.0, -3.0]
        self.assertAlmostEqual(graph.cosine_similarity(v1, v_neg), -1.0)

        # Empty or mismatched -> 0.0
        self.assertEqual(graph.cosine_similarity([], []), 0.0)
        self.assertEqual(graph.cosine_similarity([1.0], [1.0, 2.0]), 0.0)
        self.assertEqual(graph.cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_ranking_with_fake_embedder(self):
        """Ranking returns items ordered by cosine similarity descending, capped at top_k."""
        # Query vector along x-axis
        # Candidate 1: close to x-axis (sim ~0.99)
        # Candidate 2: 45 degrees (sim ~0.71)
        # Candidate 3: y-axis (sim 0.0)
        fake_vectors = {
            "the 4-billion Qwen": [1.0, 0.0, 0.0],
            "Qwen3-4B": [0.99, 0.1, 0.0],
            "Qwen2.5-Coder-3B": [0.7071, 0.7071, 0.0],
            "Unrelated Topic": [0.0, 1.0, 0.0],
            "Other Model A": [0.8, 0.2, 0.0],
            "Other Model B": [0.75, 0.25, 0.0],
            "Other Model C": [0.68, 0.3, 0.0],
        }
        embedder = graph.FakeEmbedder(vectors=fake_vectors, dim=3)
        candidates = list(fake_vectors.keys())

        # Find related items with threshold 0.70 and top_k 3
        results = graph.find_related_items(
            "the 4-billion Qwen",
            candidates,
            embedder,
            db_path=self.db_path,
            threshold=0.70,
            top_k=3,
            exact_match_filter="the 4-billion Qwen"
        )

        # Exact match filter excluded the query itself
        result_names = [name for name, score in results]
        self.assertNotIn("the 4-billion Qwen", result_names)

        # Qwen3-4B is ranked first
        self.assertEqual(result_names[0], "Qwen3-4B")
        self.assertGreater(results[0][1], 0.95)

        # Capped at top_k=3
        self.assertLessEqual(len(results), 3)

        # Strictly descending order of scores
        scores = [score for name, score in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_threshold_filtering(self):
        """Candidates below similarity threshold from tunables.json are excluded."""
        fake_vectors = {
            "query": [1.0, 0.0],
            "high_sim": [0.9, 0.1],   # sim ~0.99
            "borderline": [0.65, 0.75], # sim ~0.65
            "low_sim": [0.3, 0.95],   # sim ~0.30
        }
        embedder = graph.FakeEmbedder(vectors=fake_vectors, dim=2)
        candidates = ["high_sim", "borderline", "low_sim"]

        # Threshold 0.65: borderline (>=0.65) and high_sim included, low_sim excluded
        results = graph.find_related_items(
            "query",
            candidates,
            embedder,
            db_path=self.db_path,
            threshold=0.65,
            top_k=5
        )
        names = [name for name, _ in results]
        self.assertIn("high_sim", names)
        self.assertNotIn("low_sim", names)

    def test_embeddings_cache_sqlite(self):
        """Embeddings are cached in graph.sqlite embeddings table and reused without re-embedding."""
        fake_vectors = {
            "test item": [0.1, 0.2, 0.3],
            "candidate item": [0.4, 0.5, 0.6],
        }
        embedder = graph.FakeEmbedder(vectors=fake_vectors, dim=3)

        # First call: embedder is invoked
        results1 = graph.find_related_items(
            "test item",
            ["candidate item"],
            embedder,
            db_path=self.db_path,
            threshold=0.0
        )
        self.assertEqual(len(results1), 1)
        self.assertEqual(embedder.call_count, 2)  # 1 for query + 1 for candidate

        # Verify cached in sqlite
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT text, model, dim FROM embeddings")
        rows = cur.fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)
        cached_texts = {r[0] for r in rows}
        self.assertIn("test item", cached_texts)
        self.assertIn("candidate item", cached_texts)

        # Second call with a fresh embedder that fails if called:
        # It must succeed entirely from cache without calling the embedder!
        failing_embedder = graph.FakeEmbedder(fail=True)
        results2 = graph.find_related_items(
            "test item",
            ["candidate item"],
            failing_embedder,
            db_path=self.db_path,
            threshold=0.0
        )
        self.assertEqual(len(results2), 1)
        self.assertAlmostEqual(results1[0][1], results2[0][1])
        self.assertEqual(failing_embedder.call_count, 0)

    def test_fallback_on_embedder_failure(self):
        """Server or model failure prints one line warning and falls back to exact match without failing."""
        # Populate graph with an exact match
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute(
                "INSERT INTO facts (subject, relation, object, date, source, display) "
                "VALUES ('qwen3-4b', 'status', 'tried', '2026-11-03', 'tested_models.json', 'Qwen3-4B')"
            )
        conn.close()

        failing_embedder = graph.FakeEmbedder(fail=True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ret = si.main([
                'graph', 'query', 'Qwen3-4B',
                '--db', str(self.db_path),
                '--related'
            ])
        # Even with real embedder failure (or mock), it must return 0
        self.assertEqual(ret, 0)
        output = buf.getvalue()
        # Exact match is printed
        self.assertIn("Qwen3-4B status tried", output)

    def test_privacy_lock_on_candidates(self):
        """Private relations/entities cannot be embedded or queried unless FM_HOME is authorized."""
        conn = sqlite3.connect(self.db_path)
        with conn:
            # Public fact
            conn.execute(
                "INSERT INTO facts (subject, relation, object, date, source, display) "
                "VALUES ('qwen3-4b', 'status', 'tried', '2026-11-03', 'test', 'Qwen3-4B')"
            )
            # Private fact (outcome is not in PUBLIC_RELATIONS)
            conn.execute(
                "INSERT INTO facts (subject, relation, object, date, source, display) "
                "VALUES ('secret-internal-task', 'outcome', 'done', '2026-11-03', 'test', 'SecretTask')"
            )
        conn.close()

        # Without private=True: only public subjects appear
        public_candidates = graph.get_graph_search_candidates(db_path=self.db_path, private=False)
        self.assertIn('qwen3-4b', public_candidates)
        self.assertNotIn('secret-internal-task', public_candidates)
        self.assertNotIn('SecretTask', public_candidates)

        # With private=True but no FM_HOME -> PermissionError
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError):
                graph.get_graph_search_candidates(db_path=self.db_path, private=True)

        # With private=True and valid FM_HOME -> private candidates permitted
        with tempfile.TemporaryDirectory() as fm_dir:
            (Path(fm_dir) / 'AGENTS.md').write_text('# Agents\n', encoding='utf-8')
            with patch.dict(os.environ, {'FM_HOME': fm_dir}):
                private_candidates = graph.get_graph_search_candidates(db_path=self.db_path, private=True)
                self.assertIn('secret-internal-task', private_candidates)

    def test_proposal_check_exact_and_vector(self):
        """`si.py propose` checks for exact matches and paraphrased related proposals, logging hints."""
        # Seed existing proposals in proposals.jsonl
        props = [
            {"at": 1730000000, "source": "agent", "idea": "Auto-tune retry budget for local Qwen",
             "why": "Speed up", "status": "open"},
            {"at": 1730010000, "source": "agent", "idea": "Adopt Qwen3-4B as coding labour",
             "why": "Quality", "status": "open"},
        ]
        self.proposals_path.write_text('\n'.join(json.dumps(p) for p in props) + '\n')

        fake_vectors = {
            "Adopt Qwen3-4B as coding labour": [0.99, 0.1],
            "Use the 4-billion Qwen model for coding": [0.95, 0.15],
            "Completely unrelated idea": [0.0, 1.0],
        }
        embedder = graph.FakeEmbedder(vectors=fake_vectors, dim=2)

        # 1. Exact match test
        buf_exact = io.StringIO()
        with contextlib.redirect_stdout(buf_exact):
            si.check_related_proposals(
                "Adopt Qwen3-4B as coding labour",
                proposals_path=self.proposals_path,
                db_path=self.db_path,
                tunables_path=self.tunables_path,
                hints_path=self.hints_path,
                embedder=embedder,
            )
        out_exact = buf_exact.getvalue()
        self.assertIn("exact match: proposal #2", out_exact)

        # 2. Paraphrased proposal test
        buf_related = io.StringIO()
        with contextlib.redirect_stdout(buf_related):
            si.check_related_proposals(
                "Use the 4-billion Qwen model for coding",
                proposals_path=self.proposals_path,
                db_path=self.db_path,
                tunables_path=self.tunables_path,
                hints_path=self.hints_path,
                embedder=embedder,
            )
        out_related = buf_related.getvalue()
        self.assertIn("possibly related: proposal #2", out_related)

        # Verify hint logged in hints.jsonl
        self.assertTrue(self.hints_path.exists())
        hints = si.load_hints(hints_path=self.hints_path)
        self.assertGreaterEqual(len(hints), 1)
        self.assertEqual(hints[-1]['query'], "Use the 4-billion Qwen model for coding")
        self.assertIn("proposal:2", hints[-1]['top_hit'])
        self.assertGreater(hints[-1]['score'], 0.9)

    def test_model_record_fact_type_embedding_status_tried(self):
        """Extract facts from tested_models.json records type=embedding and status=tried."""
        tested_data = {
            "bge-small-en-v1.5": {
                "tested_at": "2026-11-03T03:02:00Z",
                "verdict": "tried",
                "type": "embedding",
                "summary_file": "/tmp/README.md"
            }
        }
        self.tested_models_path.write_text(json.dumps(tested_data))
        facts, warnings = graph.extract_facts_tested_models(self.tested_models_path)
        self.assertEqual(warnings, [])
        relations = {f[1]: f[2] for f in facts if f[0] == 'bge-small-en-v1.5'}
        self.assertEqual(relations.get('type'), 'embedding')
        self.assertEqual(relations.get('status'), 'tried')
        self.assertEqual(relations.get('verdict'), 'tried')

    def test_real_check_the_4_billion_qwen_ranks_qwen3_4b_first(self):
        """Real check against downloaded bge-small-en-v1.5 model: 'the 4-billion Qwen' ranks Qwen3-4B first."""
        model_path = Path.home() / 'local-llm' / 'models' / 'bge-small-en-v1.5' / 'bge-small-en-v1.5-q8_0.gguf'
        server_bin = Path.home() / 'local-llm' / 'llama.cpp' / 'src' / 'build' / 'bin' / 'llama-server'

        if not model_path.exists() or not server_bin.exists():
            self.skipTest("Downloaded model or llama-server not found on this machine")

        candidates = [
            "Qwen3-4B",
            "Qwen2.5-Coder-3B",
            "Qwen2.5-Coder-7B",
            "DeepSeek-Coder-1.3B",
            "Speculative Decoding",
            "Context Window",
            "Split large tasks",
        ]

        with graph.LlamaServerEmbedder(model_path=model_path, server_bin=server_bin) as embedder:
            results = graph.find_related_items(
                "the 4-billion Qwen",
                candidates,
                embedder,
                db_path=self.db_path,
                threshold=0.60,
                top_k=5,
                exact_match_filter="the 4-billion Qwen",
            )

        self.assertGreater(len(results), 0, "Expected at least 1 related candidate above threshold")
        top_hit, top_score = results[0]
        self.assertEqual(top_hit, "Qwen3-4B", f"Expected Qwen3-4B to rank first, got {top_hit} with score {top_score}")
        self.assertGreater(top_score, 0.70, f"Expected top score > 0.70, got {top_score}")


if __name__ == '__main__':
    unittest.main()
