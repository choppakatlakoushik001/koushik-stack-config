#!/usr/bin/env python3
"""Unit tests for multi-hub model monitoring adapters."""
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import hub_adapters as ha

FIXTURES_PATH = Path(__file__).parent / 'fixtures' / 'hub_fixtures.json'


class TestHubAdapters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads(FIXTURES_PATH.read_text(encoding='utf-8'))

    def test_all_twenty_hubs_registered(self):
        """Verify all 20 required hubs are registered with correct tier assignments."""
        adapters = ha.ALL_ADAPTERS
        self.assertEqual(len(adapters), 20)

        tier1 = [a for a in adapters if a.tier == 1]
        tier2 = [a for a in adapters if a.tier == 2]

        self.assertEqual(len(tier1), 10)
        self.assertEqual(len(tier2), 10)

        tier1_names = {a.name for a in tier1}
        expected_tier1 = {
            'huggingface', 'ollama', 'lmstudio', 'modelscope', 'kaggle',
            'github', 'jan', 'gpt4all', 'llamafile', 'wisemodel'
        }
        self.assertEqual(tier1_names, expected_tier1)

        tier2_names = {a.name for a in tier2}
        expected_tier2 = {
            'civitai', 'pytorch_hub', 'tfhub', 'onnx_model_zoo', 'ultralytics',
            'openmmlab', 'qualcomm_ai_hub', 'apple_coreml', 'bionemo', 'roboflow'
        }
        self.assertEqual(tier2_names, expected_tier2)

    def test_skipped_non_public_adapters_record_reason(self):
        """Adapters without unauthenticated public APIs must record skipped_reason without calling network."""
        skipped_adapters = [a for a in ha.ALL_ADAPTERS if not a.is_public_api]
        self.assertGreater(len(skipped_adapters), 0)

        for a in skipped_adapters:
            res = a.scan()
            self.assertFalse(res['checked'])
            self.assertEqual(res['hits'], [])
            self.assertIsNotNone(res['skipped_reason'])
            self.assertTrue(res['skipped_reason'].startswith('skipped:'))

    def test_tier1_adapters_fixture_parsing(self):
        """Tier 1 adapters parse coder models and detect GGUF and fit thresholds."""
        with patch("hub_adapters.time.time", return_value=1793404800.0):
            # HuggingFace
            hf = ha.HuggingFaceAdapter()
            res_hf = hf.scan(fixture_data=self.fixtures['huggingface'])
            self.assertTrue(res_hf['checked'])
            self.assertEqual(len(res_hf['hits']), 2)
            self.assertTrue(all(h['is_gguf'] for h in res_hf['hits']))
            self.assertTrue(all(h['fits'] for h in res_hf['hits']))

            # Ollama
            ol = ha.OllamaAdapter()
            res_ol = ol.scan(fixture_data=self.fixtures['ollama'])
            self.assertTrue(res_ol['checked'])
            self.assertEqual(len(res_ol['hits']), 2)

            # ModelScope
            ms = ha.ModelScopeAdapter()
            res_ms = ms.scan(fixture_data=self.fixtures['modelscope'])
            self.assertTrue(res_ms['checked'])
            self.assertGreaterEqual(len(res_ms['hits']), 1)

            # GPT4All
            gpt4all = ha.GPT4AllAdapter()
            res_g4 = gpt4all.scan(fixture_data=self.fixtures['gpt4all'])
            self.assertTrue(res_g4['checked'])
            self.assertEqual(len(res_g4['hits']), 1)
            self.assertTrue(res_g4['hits'][0]['is_gguf'])

            # Llamafile
            lf = ha.LlamafileAdapter()
            res_lf = lf.scan(fixture_data=self.fixtures['llamafile'])
            self.assertTrue(res_lf['checked'])
            self.assertEqual(len(res_lf['hits']), 1)
            self.assertTrue(res_lf['hits'][0]['is_gguf'])

    def test_tier2_adapters_keyword_queries(self):
        """Tier 2 adapters record keyword query hits and never download."""
        civ = ha.CivitaiAdapter()
        res_civ = civ.scan(fixture_data=self.fixtures['civitai'])
        self.assertTrue(res_civ['checked'])
        self.assertEqual(len(res_civ['hits']), 1)

        onnx = ha.ONNXModelZooAdapter()
        res_onnx = onnx.scan(fixture_data=self.fixtures['onnx_model_zoo'])
        self.assertTrue(res_onnx['checked'])
        self.assertEqual(len(res_onnx['hits']), 1)

    def test_cross_hub_model_deduplication(self):
        """Models appearing on multiple hubs are deduped while tracking all sightings."""
        raw_candidates = [
            {
                'hub': 'huggingface',
                'repo': 'Qwen/Qwen2.5-Coder-7B-Instruct-GGUF',
                'name': 'Qwen2.5-Coder-7B-Instruct-GGUF',
                'params': 7e9,
                'size_bytes': 4683073536,
                'fits': True,
                'is_gguf': True,
            },
            {
                'hub': 'ollama',
                'repo': 'ollama/qwen2.5-coder:7b',
                'name': 'qwen2.5-coder:7b',
                'params': 7e9,
                'size_bytes': 4700000000,
                'fits': True,
                'is_gguf': True,
            },
            {
                'hub': 'gpt4all',
                'repo': 'gpt4all/qwen2.5-coder-7b-instruct',
                'name': 'Qwen2.5 Coder 7B Instruct',
                'params': 7e9,
                'size_bytes': 4683073536,
                'fits': True,
                'is_gguf': True,
            },
            {
                'hub': 'huggingface',
                'repo': 'bartowski/Qwen2.5-Coder-1.5B-Instruct-GGUF',
                'name': 'Qwen2.5-Coder-1.5B-Instruct-GGUF',
                'params': 1.5e9,
                'size_bytes': 986000000,
                'fits': True,
                'is_gguf': True,
            }
        ]
        deduped = ha.deduplicate_candidates(raw_candidates)
        # Should deduplicate Qwen 7B to 1 entry and 1.5B to 1 entry = 2 total
        self.assertEqual(len(deduped), 2)

        qwen7b = next(c for c in deduped if '7b' in c['canonical_id'])
        self.assertEqual(len(qwen7b['hubs_seen']), 3)
        self.assertIn('huggingface', qwen7b['hubs_seen'])
        self.assertIn('ollama', qwen7b['hubs_seen'])
        self.assertIn('gpt4all', qwen7b['hubs_seen'])

    def test_scan_all_hubs_full_fixtures(self):
        """scan_all_hubs runs across all 20 hubs cleanly using fixtures."""
        scan_res = ha.scan_all_hubs(fixtures=self.fixtures)
        self.assertEqual(len(scan_res['sources']), 20)
        self.assertGreater(len(scan_res['candidates']), 0)
        self.assertGreater(len(scan_res['tier2_hits']), 0)

        # Confirm non-blocking: no adapter crashes the entire run
        for s in scan_res['sources']:
            self.assertIn('hub', s)
            self.assertIn('tier', s)
            self.assertIn('checked', s)

    def test_adapter_error_isolation(self):
        """A failure or exception in one adapter never crashes or blocks other adapters."""
        class FailingAdapter(ha.BaseHubAdapter):
            name = 'failing_hub'
            display_name = 'Failing Hub'
            tier = 1

            def fetch(self, timeout=10.0):
                raise ConnectionResetError("Simulated connection abort")

        adapter = FailingAdapter()
        res = adapter.scan()
        self.assertTrue(res['checked'])
        self.assertIsNotNone(res['error'])
        self.assertIn('Simulated connection abort', res['error'])
        self.assertEqual(res['hits'], [])

    def test_ollama_parser_html_fixture(self):
        """Ollama parser gracefully parses HTML library responses."""
        html_fixture = """<!DOCTYPE html>
        <html>
        <body>
            <a href="/library/qwen2.5-coder"><h2>qwen2.5-coder</h2></a>
            <a href="/library/deepseek-coder-v2:16b"><h2>deepseek-coder-v2:16b</h2></a>
            <a href="/library/llama3.2"><h2>llama3.2</h2></a>
        </body>
        </html>"""
        ol = ha.OllamaAdapter()
        res = ol.scan(fixture_data=html_fixture)
        self.assertTrue(res['checked'])
        self.assertEqual(len(res['hits']), 2)
        names = {h['name'] for h in res['hits']}
        self.assertIn('qwen2.5-coder', names)
        self.assertIn('deepseek-coder-v2:16b', names)
        self.assertNotIn('llama3.2', names)

    def test_jan_and_wisemodel_fixture_parsing(self):
        """Jan and WiseModel adapters parse coder models cleanly from synthetic fixtures."""
        jan = ha.JanHubAdapter()
        res_jan = jan.scan(fixture_data=self.fixtures['jan'])
        self.assertTrue(res_jan['checked'])
        self.assertEqual(len(res_jan['hits']), 1)
        self.assertTrue(res_jan['hits'][0]['is_gguf'])

        wm = ha.WiseModelAdapter()
        res_wm = wm.scan(fixture_data=self.fixtures['wisemodel'])
        self.assertTrue(res_wm['checked'])
        self.assertEqual(len(res_wm['hits']), 1)
        self.assertTrue(res_wm['hits'][0]['is_gguf'])

    def test_hub_checks_404_degrade_to_source_unavailable(self):
        """ModelScope, Jan, WiseModel, and Ollama degrade to 'source unavailable' on 404 instead of error."""
        import urllib.error
        for adapter in (ha.ModelScopeAdapter(), ha.JanHubAdapter(), ha.WiseModelAdapter(), ha.OllamaAdapter()):
            with patch.object(adapter, 'fetch', side_effect=urllib.error.HTTPError('https://example.com', 404, 'Not Found', {}, None)):
                res = adapter.scan()
                self.assertFalse(res['checked'])
                self.assertEqual(res['hits'], [])
                self.assertIsNone(res['error'])
                self.assertEqual(res['skipped_reason'], 'source unavailable')


if __name__ == '__main__':
    unittest.main()
