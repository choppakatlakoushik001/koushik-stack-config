import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import si

FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'hf_models_fixture.json'
FIXED_NOW = 1793460000  # Approx 2026-10-31 12:00:00 UTC


class TestModelsScan(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.test_root = Path(self.temp_dir.name)
        self.temp_graph = self.test_root / 'graph.sqlite'
        self.temp_tested = self.test_root / 'tested_models.json'
        self.temp_proposals = self.test_root / 'proposals.jsonl'
        self.temp_manifest = self.test_root / 'bench2-downloads.json'
        self.temp_candidate_cfg = self.test_root / 'candidate.json'
        self.temp_logs_dir = self.test_root / 'logs'
        self.temp_logs_dir.mkdir(parents=True, exist_ok=True)

        p1 = patch.object(si, 'GRAPH_DB', self.temp_graph)
        p2 = patch.object(si, 'TESTED_MODELS_DEFAULT', self.temp_tested)
        p3 = patch.object(si, 'PROPOSALS', self.temp_proposals)
        p4 = patch.object(si, 'BENCH2_DOWNLOADS_DEFAULT', self.temp_manifest)
        p5 = patch.object(si, 'BENCH2_CANDIDATE_CONFIG_DEFAULT', self.temp_candidate_cfg)
        p6 = patch.object(si, 'BENCH_LOGS_DEFAULT', self.temp_logs_dir)
        p7 = patch.object(si, 'BENCH2_HEAD_TO_HEAD_SCRIPT', self.test_root / 'nonexistent_h2h.py')
        p8 = patch.object(si.graph, 'GRAPH_DB_DEFAULT', self.temp_graph)
        p1.start()
        p2.start()
        p3.start()
        p4.start()
        p5.start()
        p6.start()
        p7.start()
        p8.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.addCleanup(p3.stop)
        self.addCleanup(p4.stop)
        self.addCleanup(p5.stop)
        self.addCleanup(p6.stop)
        self.addCleanup(p7.stop)
        self.addCleanup(p8.stop)

    def test_parse_param_count(self):
        """Extract parameter counts accurately from model identifiers."""
        self.assertEqual(si.parse_param_count("Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"), 7e9)
        self.assertEqual(si.parse_param_count("bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF"), 1.5e9)
        self.assertEqual(si.parse_param_count("Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF"), 0.5e9)
        self.assertEqual(si.parse_param_count("Qwen3-4B-GGUF"), 4e9)
        self.assertEqual(si.parse_param_count("Qwen3-8B-Q4_K_M.gguf"), 8e9)
        self.assertEqual(si.parse_param_count("unsloth/Qwen2.5-Coder-14B-Instruct-GGUF"), 14e9)
        self.assertEqual(si.parse_param_count("Qwen2.5-Coder-32B-Instruct-GGUF"), 32e9)
        self.assertIsNone(si.parse_param_count("UnknownModelWithoutParams"))

    def test_fit_math_and_threshold(self):
        """Fit filter: Q4_K_M size estimate = params * 4.85 bits / 8; keep only <= 6 GB."""
        # 3B model: 3e9 * 4.85 / 8 = 1,818,750,000 bytes (~1.82 GB / 1.69 GiB)
        size_3b = si.estimate_q4_km_size_bytes(3e9)
        self.assertAlmostEqual(size_3b, 1818750000.0, delta=1.0)
        self.assertTrue(si.model_fits_threshold(3e9))

        # 7B model: 7e9 * 4.85 / 8 = 4,243,750,000 bytes (~4.24 GB / 3.95 GiB)
        size_7b = si.estimate_q4_km_size_bytes(7e9)
        self.assertAlmostEqual(size_7b, 4243750000.0, delta=1.0)
        self.assertTrue(si.model_fits_threshold(7e9))

        # 8B model: 8e9 * 4.85 / 8 = 4,850,000,000 bytes (~4.85 GB / 4.52 GiB)
        size_8b = si.estimate_q4_km_size_bytes(8e9)
        self.assertAlmostEqual(size_8b, 4850000000.0, delta=1.0)
        self.assertTrue(si.model_fits_threshold(8e9))

        # 14B model: 14e9 * 4.85 / 8 = 8,487,500,000 bytes (~8.49 GB) > 6 GB -> Rejected
        size_14b = si.estimate_q4_km_size_bytes(14e9)
        self.assertAlmostEqual(size_14b, 8487500000.0, delta=1.0)
        self.assertFalse(si.model_fits_threshold(14e9))

        # 32B model: 32e9 * 4.85 / 8 = 19,400,000,000 bytes > 6 GB -> Rejected
        self.assertFalse(si.model_fits_threshold(32e9))

    def test_parse_hf_models_fixture(self):
        """Parse HF model listing fixture without network calls."""
        data = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
        # Reference now: 2026-10-31T12:00:00Z
        parsed = si.parse_hf_models(data, max_age_days=7, now=FIXED_NOW)

        # Repos seen within 7 days and matching coder/instruct + gguf:
        # Expected kept:
        # - Qwen/Qwen2.5-Coder-7B-Instruct-GGUF (fits)
        # - Qwen/Qwen2.5-Coder-32B-Instruct-GGUF (does not fit)
        # - bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF (fits)
        # - unsloth/Qwen2.5-Coder-14B-Instruct-GGUF (does not fit)
        # - microsoft/Phi-3.5-mini-instruct-GGUF (fits)
        # - google/gemma-2-2b-it-GGUF (fits)
        # Excluded:
        # - lmstudio-community/Qwen2.5-Coder-3B-GGUF (updated Oct 1, > 7 days old)
        # - Qwen/Qwen2-Audio-7B-GGUF (not coder or instruct)
        # - mistralai/Mistral-7B-Instruct-v0.3 (not gguf)
        repo_ids = [m['repo'] for m in parsed]
        self.assertIn('Qwen/Qwen2.5-Coder-7B-Instruct-GGUF', repo_ids)
        self.assertIn('bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF', repo_ids)
        self.assertNotIn('lmstudio-community/Qwen2.5-Coder-3B-GGUF', repo_ids)
        self.assertNotIn('Qwen/Qwen2-Audio-7B-GGUF', repo_ids)
        self.assertNotIn('mistralai/Mistral-7B-Instruct-v0.3', repo_ids)

        # Check fit flag
        fits_map = {m['repo']: m['fits'] for m in parsed}
        self.assertTrue(fits_map['Qwen/Qwen2.5-Coder-7B-Instruct-GGUF'])
        self.assertTrue(fits_map['bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF'])
        self.assertFalse(fits_map['Qwen/Qwen2.5-Coder-32B-Instruct-GGUF'])
        self.assertFalse(fits_map['unsloth/Qwen2.5-Coder-14B-Instruct-GGUF'])

        # Best candidate picking: at most one best candidate per week <= 6 GB
        best = si.pick_best_candidate(parsed)
        self.assertIsNotNone(best)
        self.assertEqual(best['repo'], 'Qwen/Qwen2.5-Coder-7B-Instruct-GGUF')

    def test_verdict_rule(self):
        """Standing rule: only positive impacts - nothing may get slower.
        A win (replace) requires never slower on any measured item AND more correct.
        A fallback-only requires more correct but slower on measured items.
        Otherwise verdict is no.
        """
        baseline = {
            'quality_pass_count': 13,
            'quality_total': 20,
            'avg_generation_tok_s': 7.27,
            'avg_ttft_s': 8.35,
            'avg_cases_wall_s': 33.23,
        }

        # Case 1: Qwen3-4B scenario from context (18/20 vs 13/20, but slower) -> fallback-only
        candidate_slower_more_accurate = {
            'quality_pass_count': 18,
            'quality_total': 20,
            'avg_generation_tok_s': 3.89,  # slower than 7.27
            'avg_ttft_s': 35.14,           # slower than 8.35
            'avg_cases_wall_s': 36.82,     # slower than 33.23
        }
        verdict, reason = si.evaluate_model_verdict(candidate_slower_more_accurate, baseline)
        self.assertEqual(verdict, 'fallback-only')
        self.assertIn('more correct', reason.lower())
        self.assertIn('slower', reason.lower())

        # Case 2: Faster AND more accurate -> replace (win!)
        candidate_win = {
            'quality_pass_count': 16,
            'quality_total': 20,
            'avg_generation_tok_s': 9.50,  # faster
            'avg_ttft_s': 7.10,            # faster
            'avg_cases_wall_s': 28.00,     # faster
        }
        verdict, reason = si.evaluate_model_verdict(candidate_win, baseline)
        self.assertEqual(verdict, 'replace')

        # Case 3: Faster but NOT more accurate (13/20 or less) -> no
        candidate_faster_same_accuracy = {
            'quality_pass_count': 13,
            'quality_total': 20,
            'avg_generation_tok_s': 12.0,
            'avg_ttft_s': 5.0,
            'avg_cases_wall_s': 20.0,
        }
        verdict, reason = si.evaluate_model_verdict(candidate_faster_same_accuracy, baseline)
        self.assertEqual(verdict, 'no')

        # Case 4: Less accurate -> no
        candidate_worse = {
            'quality_pass_count': 10,
            'quality_total': 20,
            'avg_generation_tok_s': 15.0,
            'avg_ttft_s': 4.0,
            'avg_cases_wall_s': 18.0,
        }
        verdict, reason = si.evaluate_model_verdict(candidate_worse, baseline)
        self.assertEqual(verdict, 'no')

    def test_models_scan_advancements_logging(self):
        """Running models_scan appends findings to advancements.jsonl and handles network errors gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adv_file = Path(tmpdir) / 'advancements.jsonl'
            res = si.models_scan(
                fixture_path=FIXTURE_PATH,
                advancements_path=adv_file,
                now=FIXED_NOW,
            )
            self.assertTrue(adv_file.exists())
            lines = [json.loads(l) for l in adv_file.read_text().splitlines() if l.strip()]
            self.assertGreater(len(lines), 0)
            self.assertEqual(res['best_candidate']['repo'], 'Qwen/Qwen2.5-Coder-7B-Instruct-GGUF')

            # Test network error is recorded and non-fatal
            def failing_fetcher(author):
                raise ConnectionError("Hugging Face API simulated timeout")

            res_error = si.models_scan(
                fetch=True,
                fetcher=failing_fetcher,
                advancements_path=adv_file,
                now=FIXED_NOW,
            )
            # Must not raise; network failures recorded
            self.assertGreater(len(res_error['network_errors']), 0)

    def test_resolve_hf_candidate_success_and_refusal(self):
        """No invented metadata: resolve sha and byte_size strictly, refuse otherwise."""
        valid_model_data = {
            'id': 'Qwen/Qwen3-4B-GGUF',
            'sha': 'bc640142c66e1fdd12af0bd68f40445458f3869b',
            'siblings': [
                {'rfilename': 'README.md', 'size': 1200},
                {'rfilename': 'Qwen3-4B-Q4_K_M.gguf', 'size': 2497280256},
            ]
        }
        resolved = si.resolve_hf_candidate(valid_model_data)
        self.assertEqual(resolved['repo'], 'Qwen/Qwen3-4B-GGUF')
        self.assertEqual(resolved['file'], 'Qwen3-4B-Q4_K_M.gguf')
        self.assertEqual(resolved['revision'], 'bc640142c66e1fdd12af0bd68f40445458f3869b')
        self.assertEqual(resolved['byte_size'], 2497280256)
        self.assertEqual(resolved['ngl'], 24)

        # Refusal 1: Missing sha
        missing_sha = {
            'id': 'Qwen/Qwen3-4B-GGUF',
            'siblings': [{'rfilename': 'Qwen3-4B-Q4_K_M.gguf', 'size': 2497280256}]
        }
        with self.assertRaises(ValueError):
            si.resolve_hf_candidate(missing_sha)

        # Refusal 2: Placeholder 'pinned_default' or 'unknown'
        placeholder_sha = {
            'id': 'Qwen/Qwen3-4B-GGUF',
            'sha': 'pinned_default',
            'siblings': [{'rfilename': 'Qwen3-4B-Q4_K_M.gguf', 'size': 2497280256}]
        }
        with self.assertRaises(ValueError):
            si.resolve_hf_candidate(placeholder_sha)

        # Refusal 3: Missing Q4_K_M GGUF file
        no_gguf = {
            'id': 'Qwen/Qwen3-4B-GGUF',
            'sha': 'bc640142c66e1fdd12af0bd68f40445458f3869b',
            'siblings': [{'rfilename': 'README.md', 'size': 1200}]
        }
        with self.assertRaises(ValueError):
            si.resolve_hf_candidate(no_gguf)

        # Refusal 4: Missing or zero byte size (no placeholder allowed)
        no_size = {
            'id': 'Qwen/Qwen3-4B-GGUF',
            'sha': 'bc640142c66e1fdd12af0bd68f40445458f3869b',
            'siblings': [{'rfilename': 'Qwen3-4B-Q4_K_M.gguf', 'size': 0}]
        }
        with self.assertRaises(ValueError):
            si.resolve_hf_candidate(no_size)

    def test_tested_models_tracking_and_candidate_exclusion(self):
        """Maintain a tested list and exclude already-tested models from candidate picking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tested_file = Path(tmpdir) / 'tested_models.json'

            # Initially empty
            self.assertEqual(si.load_tested_models(tested_file), {})

            # Record a tested model
            si.record_tested_model(
                'Qwen/Qwen2.5-Coder-7B-Instruct-GGUF',
                verdict='fallback-only',
                summary_file='/tmp/qwen7b_summary.json',
                path=tested_file,
            )
            loaded = si.load_tested_models(tested_file)
            self.assertIn('Qwen/Qwen2.5-Coder-7B-Instruct-GGUF', loaded)
            self.assertEqual(loaded['Qwen/Qwen2.5-Coder-7B-Instruct-GGUF']['verdict'], 'fallback-only')

            # Parse fixture data
            fixture_data = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
            parsed = si.parse_hf_models(fixture_data, max_age_days=7, now=FIXED_NOW)

            # Untested picking: 7B was highest score, but now it is in tested_models!
            # The picker must skip 7B and pick the next best fitting candidate (1.5B or gemma-2-2b)
            next_best = si.pick_best_candidate(parsed, tested_models_path=tested_file)
            self.assertIsNotNone(next_best)
            self.assertNotEqual(next_best['repo'], 'Qwen/Qwen2.5-Coder-7B-Instruct-GGUF')
            self.assertEqual(next_best['repo'], 'bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF')

    def test_models_test_dry_run(self):
        """si.py models-test --dry-run queues without running inline, recording to manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_file = Path(tmpdir) / 'bench2-downloads.json'
            candidate_cfg = Path(tmpdir) / 'candidate.json'
            logs_dir = Path(tmpdir) / 'logs'
            logs_dir.mkdir(parents=True, exist_ok=True)

            candidate_meta = {
                'id': 'deepseek-coder-1.3b',
                'repo': 'deepseek-ai/DeepSeek-Coder-1.3B-GGUF',
                'file': 'DeepSeek-Coder-1.3B-Q4_K_M.gguf',
                'revision': 'bc640142c66e1fdd12af0bd68f40445458f3869b',
                'byte_size': 1234567890,
            }
            res = si.models_test(
                candidate=candidate_meta,
                dry_run=True,
                manifest_path=manifest_file,
                candidate_config_path=candidate_cfg,
                logs_dir=logs_dir,
                graph_path=self.temp_graph,
                tested_models_path=self.temp_tested,
                proposals_path=self.temp_proposals,
            )
            self.assertTrue(res['queued'])
            self.assertTrue(manifest_file.exists())
            self.assertTrue(candidate_cfg.exists())

            manifest = json.loads(manifest_file.read_text())
            dest_key = str(Path('/home/chopp/local-llm/models') / 'DeepSeek-Coder-1.3B-GGUF' / 'DeepSeek-Coder-1.3B-Q4_K_M.gguf')
            self.assertIn(dest_key, manifest)
            self.assertEqual(manifest[dest_key]['pinned_revision_hash'], 'bc640142c66e1fdd12af0bd68f40445458f3869b')

            # Verify candidate config written for bench2
            cfg_data = json.loads(candidate_cfg.read_text())
            self.assertEqual(cfg_data['repo'], 'deepseek-ai/DeepSeek-Coder-1.3B-GGUF')
            self.assertEqual(cfg_data['file'], 'DeepSeek-Coder-1.3B-Q4_K_M.gguf')
            self.assertEqual(cfg_data['revision'], 'bc640142c66e1fdd12af0bd68f40445458f3869b')
            self.assertEqual(cfg_data['byte_size'], 1234567890)

    def test_declined_model_is_not_retested(self):
        """A model already declined (such as Qwen3-4B) is skipped and not re-tested."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_file = Path(tmpdir) / 'bench2-downloads.json'
            candidate_declined = {
                'id': 'qwen3-4b',
                'repo': 'Qwen/Qwen3-4B-GGUF',
                'file': 'Qwen3-4B-Q4_K_M.gguf',
                'revision': 'bc640142c66e1fdd12af0bd68f40445458f3869b',
                'byte_size': 2497280256,
            }
            # Record explicit decline decision in synthetic tested_models.json
            self.temp_tested.write_text(json.dumps({
                'Qwen/Qwen3-4B-GGUF': {
                    'verdict': 'declined',
                    'tested_at': '2026-10-31T15:00:00Z',
                    'summary_file': '/tmp/qwen3_4b_summary.json',
                }
            }, indent=2) + '\n')
            res = si.models_test(
                candidate=candidate_declined,
                dry_run=True,
                manifest_path=manifest_file,
                graph_path=self.temp_graph,
                tested_models_path=self.temp_tested,
                proposals_path=self.temp_proposals,
            )
            self.assertFalse(res['queued'])
            self.assertTrue(res.get('declined'))
            self.assertIn('already declined', res.get('reason'))
            self.assertFalse(manifest_file.exists())

    def test_sunday_review_auto_queues_candidate(self):
        """Sunday review fetches candidates and auto-queues best untested candidate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            rep_dir = tmp / 'reports'
            rep_dir.mkdir(parents=True, exist_ok=True)
            tested_file = tmp / 'tested.json'
            adv_file = tmp / 'adv.jsonl'
            manifest_file = tmp / 'manifest.json'

            fixture_data = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))

            # Mock scan to return parsed candidate
            parsed = si.parse_hf_models(fixture_data, max_age_days=7, now=FIXED_NOW)
            best = si.pick_best_candidate(parsed)
            resolved_best = si.resolve_hf_candidate({
                'repo': best['repo'],
                'file': 'qwen2.5-coder-7b-instruct-q4_k_m.gguf',
                'revision': '13fb94bfda8c8cf22497dc57b78f391a9acb426a',
                'byte_size': 4683073536,
            })

            queued_calls = []

            def mock_models_test(candidate, dry_run=False, **kwargs):
                queued_calls.append(candidate)
                return {'queued': True, 'candidate': candidate}

            def mock_models_scan(fetch=True, **kwargs):
                return {
                    'seen': parsed,
                    'qualifying': [c for c in parsed if c['fits']],
                    'best_candidate': best,
                    'best_resolved': resolved_best,
                    'network_errors': [],
                }

            with patch('si.REPORTS', rep_dir), \
                 patch('si.tune'), \
                 patch('si.notify_review_ready'), \
                 patch('si.models_scan', side_effect=mock_models_scan), \
                 patch('si.models_test', side_effect=mock_models_test):
                args = type('Args', (), {'days': 7})()
                si.cmd_review(args)

            self.assertEqual(len(queued_calls), 1)
            self.assertEqual(queued_calls[0]['repo'], 'Qwen/Qwen2.5-Coder-7B-Instruct-GGUF')

            # Verify report was generated and contains section
            reports = list(rep_dir.glob('*.md'))
            self.assertEqual(len(reports), 1)
            report_text = reports[0].read_text()
            self.assertIn('## New coder models', report_text)
            self.assertIn('### Seen', report_text)

    def test_review_does_not_post_to_real_inbox_in_tests(self):
        """Review must never touch the real inbox during tests: stubbing and env redirection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stub_file = Path(tmpdir) / 'stubbed_inbox.txt'
            dummy_report = Path(tmpdir) / 'dummy_review.md'
            dummy_report.write_text('# Review\n', encoding='utf-8')

            # Case 1: FM_NOTIFY_DISABLE=1 -> subprocess.run is never called, no note written
            with patch.dict(os.environ, {'FM_NOTIFY_DISABLE': '1'}), patch('si.subprocess.run') as mock_run:
                si.notify_review_ready(dummy_report)
                mock_run.assert_not_called()
                self.assertFalse(stub_file.exists())

            # Case 2: SELFIMPROVE_TESTING=1 -> subprocess.run is never called
            with patch.dict(os.environ, {'SELFIMPROVE_TESTING': '1'}), patch('si.subprocess.run') as mock_run:
                si.notify_review_ready(dummy_report)
                mock_run.assert_not_called()
                self.assertFalse(stub_file.exists())

            # Case 3: FM_INBOX_STUB set -> redirected to test file, subprocess.run not called
            with patch.dict(os.environ, {'FM_INBOX_STUB': str(stub_file)}), patch('si.subprocess.run') as mock_run:
                si.notify_review_ready(dummy_report)
                mock_run.assert_not_called()
                self.assertTrue(stub_file.exists())
                self.assertIn('Weekly self-improvement review ready', stub_file.read_text())

    def test_review_includes_new_coder_models_section(self):
        """Review report includes 'New coder models' section with seen/tested/numbers/verdict."""
        candidate = {
            'name': 'Qwen3-4B',
            'repo': 'Qwen/Qwen3-4B-GGUF',
            'size_gb': 2.33,
            'ngl': 24,
            'quality_pass_count': 18,
            'quality_total': 20,
            'avg_generation_tok_s': 7.73,
            'avg_ttft_s': 19.78,
        }
        baseline = {
            'name': 'Qwen2.5-Coder-3B (Production ngl=24)',
            'repo': 'Qwen/Qwen2.5-Coder-3B-Instruct',
            'size_gb': 1.99,
            'ngl': 24,
            'quality_pass_count': 13,
            'quality_total': 20,
            'avg_generation_tok_s': 9.72,
            'avg_ttft_s': 8.42,
        }
        lines = si.new_coder_models_lines(
            seen=[{'repo': 'Qwen/Qwen3-4B-GGUF', 'size_gb': 2.33, 'fits': True}],
            candidate=candidate,
            baseline=baseline,
            verdict='fallback-only',
            reason='More accurate (18/20 vs 13/20) but slower generation than production baseline.',
        )
        report_text = '\n'.join(lines)
        self.assertIn('## New coder models', report_text)
        self.assertIn('### Seen', report_text)
        self.assertIn('### Tested', report_text)
        self.assertIn('### Numbers', report_text)
        self.assertIn('### Verdict', report_text)
        self.assertIn('Qwen2.5-Coder-3B (Production ngl=24)', report_text)
        self.assertIn('fallback-only', report_text)
        self.assertIn('Never change the default model or qwen-server settings', report_text)

    def test_cli_models_scan_and_test(self):
        """CLI invocation of models-scan and models-test."""
        with tempfile.TemporaryDirectory() as tmpdir:
            adv_file = Path(tmpdir) / 'adv.jsonl'
            manifest_file = Path(tmpdir) / 'manifest.json'

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = si.main([
                    'models-scan',
                    '--fixture', str(FIXTURE_PATH),
                    '--advancements', str(adv_file),
                    '--graph', str(self.temp_graph),
                    '--tested-models', str(self.temp_tested),
                ])
            self.assertEqual(code, 0)
            self.assertIn('Models scan complete', out.getvalue())
            self.assertIn('[BEST CANDIDATE]', out.getvalue())

            out2 = io.StringIO()
            with contextlib.redirect_stdout(out2):
                code2 = si.main([
                    'models-test',
                    '--dry-run',
                    '--manifest', str(manifest_file),
                    '--graph', str(self.temp_graph),
                    '--tested-models', str(self.temp_tested),
                    '--proposals', str(self.temp_proposals),
                ])
            self.assertEqual(code2, 0)
            self.assertIn('Dry-run: candidate A/B job prepared', out2.getvalue())
            self.assertIn('jp-guard queue add-exec run-abtest', out2.getvalue())


if __name__ == '__main__':
    unittest.main()
