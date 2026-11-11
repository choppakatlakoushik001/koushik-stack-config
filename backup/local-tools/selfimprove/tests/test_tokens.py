"""Tests for weekly token-consumption tracking, combinations, and concepts reporting.
All tests use synthetic fixtures only; no real logs or private data.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import token_records
import si


class TestTokenTracking(unittest.TestCase):
    def test_weighted_cost_calculation(self):
        # input: 1000 * 1.0 = 1000
        # cache_read: 2000 * 0.1 = 200
        # cache_write: 400 * 1.25 = 500
        # output: 100 * 5.0 = 500
        # Total = 1000 + 200 + 500 + 500 = 2200.0
        cost = token_records.calculate_weighted_cost(1000, 2000, 400, 100)
        self.assertEqual(cost, 2200.0)

    def test_stream_claude_records_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            proj = root / '-home-user-project'
            proj.mkdir()
            jsonl = proj / 'session1.jsonl'
            lines = [
                json.dumps({
                    'timestamp': '2026-10-25T12:00:00Z',
                    'cwd': '/home/user/worktree1',
                    'sessionId': 's1',
                    'message': {
                        'model': 'claude-3-5-sonnet',
                        'usage': {
                            'input_tokens': 100,
                            'cache_read_input_tokens': 200,
                            'cache_creation_input_tokens': 50,
                            'output_tokens': 30,
                        },
                    },
                }),
                # Event outside time window
                json.dumps({
                    'timestamp': '2026-10-10T12:00:00Z',
                    'cwd': '/home/user/worktree1',
                    'sessionId': 's1',
                    'message': {
                        'usage': {'input_tokens': 999},
                    },
                }),
            ]
            jsonl.write_text('\n'.join(lines) + '\n', encoding='utf-8')

            start_t = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
            end_t = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
            records = token_records.stream_claude_records(root, start_t, end_t)

            self.assertEqual(len(records), 1)
            r = records[0]
            self.assertEqual(r['cli'], 'claude')
            self.assertEqual(r['input'], 100)
            self.assertEqual(r['cache_read'], 200)
            self.assertEqual(r['cache_write'], 50)
            self.assertEqual(r['output'], 30)
            self.assertEqual(r['total'], 380)
            self.assertEqual(r['cwd'], '/home/user/worktree1')
            self.assertEqual(r['session_id'], 's1')

    def test_stream_codex_records_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sess_dir = root / '2026' / '10' / '25'
            sess_dir.mkdir(parents=True)
            jsonl = sess_dir / 'rollout-session.jsonl'
            lines = [
                json.dumps({
                    'type': 'session_meta',
                    'payload': {
                        'id': 'codex-sess-1',
                        'cwd': '/home/user/worktree-codex',
                    },
                }),
                json.dumps({
                    'type': 'token_usage_record',
                    'timestamp': '2026-10-25T14:00:00Z',
                    'payload': {
                        'session_id': 'codex-sess-1',
                        'usage': {
                            'input_tokens': 500,
                            'cached_input_tokens': 300,
                            'cache_write_input_tokens': 20,
                            'output_tokens': 40,
                        },
                    },
                }),
            ]
            jsonl.write_text('\n'.join(lines) + '\n', encoding='utf-8')

            start_t = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
            end_t = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
            records = token_records.stream_codex_records(root, start_t, end_t)

            self.assertEqual(len(records), 1)
            r = records[0]
            self.assertEqual(r['cli'], 'codex')
            self.assertEqual(r['input'], 500)
            self.assertEqual(r['cache_read'], 300)
            self.assertEqual(r['cache_write'], 20)
            self.assertEqual(r['output'], 40)
            self.assertEqual(r['cwd'], '/home/user/worktree-codex')

    def test_stream_agy_records_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / 'brain-uuid-1' / '.system_generated' / 'logs'
            log_dir.mkdir(parents=True)
            jsonl = log_dir / 'transcript.jsonl'
            lines = [
                json.dumps({
                    'step_index': 1,
                    'type': 'GENERIC',
                    'tool_calls': [{
                        'function': {
                            'arguments': json.dumps({'CommandLine': '( cd /home/user/.treehouse/pool/1/slot && git status )'})
                        }
                    }]
                }),
                json.dumps({
                    'step_index': 2,
                    'type': 'PLANNER_RESPONSE',
                    'created_at': '2026-10-25T15:00:00Z',
                    'input_tokens': 1200,
                    'cache_read_tokens': 800,
                    'output_tokens': 150,
                }),
            ]
            jsonl.write_text('\n'.join(lines) + '\n', encoding='utf-8')

            start_t = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
            end_t = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
            records = token_records.stream_agy_records(root, start_t, end_t)

            self.assertEqual(len(records), 1)
            r = records[0]
            self.assertEqual(r['cli'], 'agy')
            self.assertEqual(r['input'], 1200)
            self.assertEqual(r['cache_read'], 800)
            self.assertEqual(r['output'], 150)
            self.assertEqual(r['cwd'], '/home/user/.treehouse/pool/1/slot')

    def test_stream_qwen_records_top_level_and_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Case 1: Top-level prompt_tokens and completion_tokens (qwen_task.py format)
            res1 = root / '20261025T160000-top'
            res1.mkdir()
            (res1 / 'spec.json').write_text(json.dumps({'repo': '/home/user/worktree-qwen-1'}), encoding='utf-8')
            (res1 / 'verdict.json').write_text(
                json.dumps({
                    'verdict': 'pass',
                    'prompt_tokens': 600,
                    'completion_tokens': 120,
                    'jobs': [{'attempt': 1}],  # jobs has no token counts
                }),
                encoding='utf-8'
            )

            # Case 2: Fallback to jobs[] (fill.py format)
            res2 = root / '20261025T170000-fallback'
            res2.mkdir()
            (res2 / 'spec.json').write_text(json.dumps({'repo': '/home/user/worktree-qwen-2'}), encoding='utf-8')
            (res2 / 'verdict.json').write_text(
                json.dumps({
                    'verdict': 'pass',
                    'jobs': [
                        {'attempt': 1, 'prompt_tokens': 450, 'completion_tokens': 80},
                        {'attempt': 2, 'prompt_tokens': 500, 'completion_tokens': 95},
                    ],
                }),
                encoding='utf-8'
            )

            start_t = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()
            end_t = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
            records = sorted(token_records.stream_qwen_records(root, start_t, end_t), key=lambda x: x['input'])

            self.assertEqual(len(records), 2)
            # res1 (top-level)
            r1 = records[0]
            self.assertEqual(r1['input'], 600)
            self.assertEqual(r1['output'], 120)
            self.assertEqual(r1['cwd'], '/home/user/worktree-qwen-1')

            # res2 (fallback to jobs)
            r2 = records[1]
            self.assertEqual(r2['input'], 950)
            self.assertEqual(r2['output'], 175)
            self.assertEqual(r2['cwd'], '/home/user/worktree-qwen-2')

    def test_master_labour_combinations_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            claude_dir = root / 'claude'
            codex_dir = root / 'codex'
            agy_dir = root / 'agy'
            gemini_dir = root / 'gemini'
            qwen_dir = root / 'qwen'
            fm_dir = root / 'fm_state'
            for d in (claude_dir, codex_dir, agy_dir, gemini_dir, qwen_dir, fm_dir):
                d.mkdir()

            now_dt = datetime(2026, 10, 25, 13, 0, 0, tzinfo=timezone.utc)
            now_ts = now_dt.timestamp()
            task_ts = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc).timestamp()

            # Create fleet-ledger entry
            ledger_file = fm_dir / 'fleet-ledger.jsonl'
            ledger_file.write_text(
                json.dumps({'task': 'task-1', 'ts': task_ts}) + '\n' +
                json.dumps({'task': 'task-codex-alone', 'ts': task_ts + 100}) + '\n',
                encoding='utf-8'
            )

            # Create Firstmate task: agy master + qwen labour
            wt_path = '/home/user/.treehouse/task-1'
            meta_text = '\n'.join([
                'harness=agy',
                'model=claude-sonnet-4-6',
                f'worktree={wt_path}',
                f'spawn_gen=s{int(task_ts)}',
                '',
            ])
            (fm_dir / 'task-1.meta').write_text(meta_text, encoding='utf-8')

            # Create agy worker session in this worktree
            agy_log = agy_dir / 'sess1' / '.system_generated' / 'logs'
            agy_log.mkdir(parents=True)
            (agy_log / 'transcript.jsonl').write_text(
                json.dumps({
                    'step_index': 1,
                    'type': 'GENERIC',
                    'tool_calls': [{'function': {'arguments': json.dumps({'CommandLine': f'( cd {wt_path} )'})}}]
                }) + '\n' +
                json.dumps({
                    'step_index': 2,
                    'type': 'PLANNER_RESPONSE',
                    'created_at': '2026-10-25T12:05:00Z',
                    'input_tokens': 1000,
                    'cache_read_tokens': 500,
                    'output_tokens': 100,
                }) + '\n',
                encoding='utf-8'
            )

            # Create Qwen run in this worktree
            qwen_run = qwen_dir / '20261025T121000-abcd1234'
            qwen_run.mkdir()
            (qwen_run / 'spec.json').write_text(json.dumps({'repo': wt_path}), encoding='utf-8')
            (qwen_run / 'verdict.json').write_text(
                json.dumps({'prompt_tokens': 300, 'completion_tokens': 50}),
                encoding='utf-8'
            )

            # Create a Codex worker session in another treehouse worktree with NO Qwen runs (Codex alone)
            wt_codex = '/home/user/.treehouse/task-codex-alone'
            cdx_day = codex_dir / '2026' / '10' / '25'
            cdx_day.mkdir(parents=True)
            (cdx_day / 'codex-sess.jsonl').write_text(
                json.dumps({
                    'type': 'session_meta',
                    'payload': {'id': 'codex-alone-1', 'cwd': wt_codex},
                }) + '\n' +
                json.dumps({
                    'type': 'token_usage_record',
                    'timestamp': '2026-10-25T12:15:00Z',
                    'payload': {
                        'session_id': 'codex-alone-1',
                        'usage': {'input_tokens': 400, 'output_tokens': 50},
                    },
                }) + '\n',
                encoding='utf-8'
            )

            # Create an unattributed Claude supervisor session in a non-treehouse directory
            cl_proj = claude_dir / '-home-user-unattributed'
            cl_proj.mkdir()
            (cl_proj / 'unattr.jsonl').write_text(
                json.dumps({
                    'timestamp': '2026-10-25T12:00:00Z',
                    'cwd': '/home/user/kunchen_firstmate',
                    'message': {'usage': {'input_tokens': 200, 'output_tokens': 50}},
                }) + '\n',
                encoding='utf-8'
            )

            stats = token_records.aggregate_weekly_tokens(
                days=7,
                now=now_ts,
                claude_dir=claude_dir,
                codex_dir=codex_dir,
                agy_dir=agy_dir,
                gemini_dir=gemini_dir,
                qwen_dir=qwen_dir,
                fm_state_dir=fm_dir,
                fleet_ledger_path=ledger_file,
            )

            # Verify agy+Qwen combination
            self.assertIn(('agy+Qwen', 'claude-sonnet-4-6'), stats['combinations'])
            comb_agy = stats['combinations'][('agy+Qwen', 'claude-sonnet-4-6')]
            self.assertEqual(comb_agy['tasks'], 1)
            self.assertEqual(comb_agy['master_tokens'], 1600)
            self.assertEqual(comb_agy['labour_tokens'], 350)
            self.assertEqual(comb_agy['total_tokens'], 1950)

            # Verify Codex alone combination
            self.assertIn(('Codex alone', 'codex'), stats['combinations'])
            comb_cdx = stats['combinations'][('Codex alone', 'codex')]
            self.assertEqual(comb_cdx['tasks'], 1)
            self.assertEqual(comb_cdx['master_tokens'], 450)
            self.assertEqual(comb_cdx['labour_tokens'], 0)
            self.assertEqual(comb_cdx['total_tokens'], 450)

            # Verify Claude supervisor master is unattributed
            self.assertEqual(stats['unattributed']['claude']['total'], 250)

            # Render report lines
            lines = token_records.token_report_lines(stats)
            rendered = '\n'.join(lines)
            self.assertIn('## Weekly token consumption', rendered)
            self.assertIn('| Configuration | Model | Tasks | Master tokens | Labour tokens | Total |', rendered)
            self.assertIn('| agy+Qwen | claude-sonnet-4-6 | 1 | 1,600 | 350 | 1,950 |', rendered)
            self.assertIn('| Codex alone | codex | 1 | 450 | 0 | 450 |', rendered)
            self.assertIn('| Claude (supervisor master) | 250 |', rendered)
            self.assertIn('| Grok | disabled |', rendered)
            self.assertIn('| Gemini | not measured |', rendered)
            self.assertIn('Note: Gemini usage not measured', rendered)
            self.assertIn('Change vs last week', rendered)

    def test_format_tool_change(self):
        # Positive change
        diff, pct, s = token_records.format_tool_change(1200, 1000)
        self.assertEqual(diff, 200)
        self.assertEqual(pct, 20.0)
        self.assertEqual(s, '+200 (+20.0%)')

        # Negative change
        diff, pct, s = token_records.format_tool_change(800, 1000)
        self.assertEqual(diff, -200)
        self.assertEqual(pct, -20.0)
        self.assertEqual(s, '-200 (-20.0%)')

        # No change
        diff, pct, s = token_records.format_tool_change(1000, 1000)
        self.assertEqual(diff, 0)
        self.assertEqual(pct, 0.0)
        self.assertEqual(s, '+0 (+0.0%)')

        # No prior baseline
        diff, pct, s = token_records.format_tool_change(500, None)
        self.assertIsNone(diff)
        self.assertIsNone(pct)
        self.assertEqual(s, 'N/A (no baseline)')

        # Currently not measured
        diff, pct, s = token_records.format_tool_change(None, 500)
        self.assertIsNone(diff)
        self.assertIsNone(pct)
        self.assertEqual(s, 'not measured')

        # Zero previous to positive
        diff, pct, s = token_records.format_tool_change(100, 0)
        self.assertEqual(diff, 100)
        self.assertEqual(pct, 100.0)
        self.assertEqual(s, '+100 (+100.0%)')

        # Zero to zero
        diff, pct, s = token_records.format_tool_change(0, 0)
        self.assertEqual(diff, 0)
        self.assertEqual(pct, 0.0)
        self.assertEqual(s, '+0 (+0.0%)')

    def test_previous_totals_persistence_across_reset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tokens_file = root / 'tokens.jsonl'
            prev_file = root / 'tokens_previous.json'

            tokens_file.write_text(
                json.dumps({'cli': 'claude', 'total': 500}) + '\n' +
                json.dumps({'cli': 'codex', 'total': 300}) + '\n',
                encoding='utf-8'
            )

            # Reset derives totals from records and saves to prev_file
            token_records.reset_weekly_token_records(tokens_file, prev_totals_path=prev_file)
            self.assertFalse(tokens_file.exists())
            self.assertTrue(prev_file.exists())

            loaded = token_records.load_previous_token_totals(prev_file)
            self.assertEqual(loaded.get('claude'), 500)
            self.assertEqual(loaded.get('codex'), 300)

            # Explicit current_totals save
            token_records.save_previous_token_totals(
                {'claude': {'total': 800, 'status': 'active'}, 'codex': {'total': 250, 'status': 'active'}, 'gemini': {'status': 'not measured'}},
                path=prev_file
            )
            loaded2 = token_records.load_previous_token_totals(prev_file)
            self.assertEqual(loaded2.get('claude'), 800)
            self.assertEqual(loaded2.get('codex'), 250)
            self.assertIsNone(loaded2.get('gemini'))

    def test_report_shows_change_vs_previous_week(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prev_file = root / 'tokens_previous.json'
            prev_file.write_text(
                json.dumps({'totals': {'claude': 1000, 'codex': 500, 'agy': 200, 'qwen': 50}}),
                encoding='utf-8'
            )

            claude_dir = root / 'claude'
            codex_dir = root / 'codex'
            agy_dir = root / 'agy'
            gemini_dir = root / 'gemini'
            qwen_dir = root / 'qwen'
            for d in (claude_dir, codex_dir, agy_dir, gemini_dir, qwen_dir):
                d.mkdir()

            now_dt = datetime(2026, 10, 25, 13, 0, 0, tzinfo=timezone.utc)
            now_ts = now_dt.timestamp()

            # Claude session: 1200 total (+200, +20.0%)
            cl_proj = claude_dir / '-proj'
            cl_proj.mkdir()
            (cl_proj / 's.jsonl').write_text(
                json.dumps({
                    'timestamp': '2026-10-25T12:00:00Z',
                    'message': {'usage': {'input_tokens': 1000, 'output_tokens': 200}},
                }) + '\n',
                encoding='utf-8'
            )

            # Codex session: 400 total (-100, -20.0%)
            cdx_day = codex_dir / '2026' / '10' / '25'
            cdx_day.mkdir(parents=True)
            (cdx_day / 's.jsonl').write_text(
                json.dumps({
                    'type': 'token_usage_record',
                    'timestamp': '2026-10-25T12:00:00Z',
                    'payload': {'usage': {'input_tokens': 350, 'output_tokens': 50}},
                }) + '\n',
                encoding='utf-8'
            )

            # agy session: 200 total (+0, +0.0%)
            agy_log = agy_dir / 's1' / '.system_generated' / 'logs'
            agy_log.mkdir(parents=True)
            (agy_log / 'transcript.jsonl').write_text(
                json.dumps({
                    'type': 'PLANNER_RESPONSE',
                    'created_at': '2026-10-25T12:00:00Z',
                    'input_tokens': 180,
                    'output_tokens': 20,
                }) + '\n',
                encoding='utf-8'
            )

            # Qwen run: 60 total (+10, +20.0%)
            qw_run = qwen_dir / '20261025T120000-case'
            qw_run.mkdir()
            (qw_run / 'verdict.json').write_text(
                json.dumps({'prompt_tokens': 50, 'completion_tokens': 10}),
                encoding='utf-8'
            )

            stats = token_records.aggregate_weekly_tokens(
                days=7,
                now=now_ts,
                claude_dir=claude_dir,
                codex_dir=codex_dir,
                agy_dir=agy_dir,
                gemini_dir=gemini_dir,
                qwen_dir=qwen_dir,
                prev_totals_path=prev_file,
            )

            self.assertEqual(stats['per_cli']['claude']['change'], '+200 (+20.0%)')
            self.assertEqual(stats['per_cli']['codex']['change'], '-100 (-20.0%)')
            self.assertEqual(stats['per_cli']['agy']['change'], '+0 (+0.0%)')
            self.assertEqual(stats['per_cli']['qwen']['change'], '+10 (+20.0%)')
            self.assertEqual(stats['per_cli']['gemini']['change'], 'not measured')

            lines = token_records.token_report_lines(stats)
            rendered = '\n'.join(lines)
            self.assertIn('| Change vs last week |', rendered)
            self.assertIn('| Claude | active | 1,000 | 0 | 0 | 200 | 1,200 | +200 (+20.0%) |', rendered)
            self.assertIn('| Codex | active | 350 | 0 | 0 | 50 | 400 | -100 (-20.0%) |', rendered)
            self.assertIn('| agy (Antigravity) | active | 180 | 0 | 0 | 20 | 200 | +0 (+0.0%) |', rendered)
            self.assertIn('| Qwen (local) | active | 50 | N/A | N/A | 10 | 60 | +10 (+20.0%) |', rendered)
            self.assertIn('| Gemini | not measured | not measured |', rendered)
            self.assertIn('> Note: Gemini usage not measured', rendered)

    def test_gemini_measured_when_local_records_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prev_file = root / 'tokens_previous.json'
            prev_file.write_text(
                json.dumps({'totals': {'gemini': 400}}),
                encoding='utf-8'
            )

            gemini_dir = root / 'gemini'
            gemini_dir.mkdir()
            (gemini_dir / 'session.jsonl').write_text(
                json.dumps({
                    'timestamp': '2026-10-25T12:00:00Z',
                    'input_tokens': 500,
                    'output_tokens': 50,
                }) + '\n',
                encoding='utf-8'
            )

            now_ts = datetime(2026, 10, 25, 13, 0, 0, tzinfo=timezone.utc).timestamp()
            stats = token_records.aggregate_weekly_tokens(
                days=7,
                now=now_ts,
                claude_dir=root / 'empty_cl',
                codex_dir=root / 'empty_cdx',
                agy_dir=root / 'empty_agy',
                gemini_dir=gemini_dir,
                qwen_dir=root / 'empty_qw',
                prev_totals_path=prev_file,
            )

            self.assertEqual(stats['per_cli']['gemini']['status'], 'active')
            self.assertEqual(stats['per_cli']['gemini']['total'], 550)
            self.assertEqual(stats['per_cli']['gemini']['change'], '+150 (+37.5%)')

            lines = token_records.token_report_lines(stats)
            rendered = '\n'.join(lines)
            self.assertIn('| Gemini | active | 500 | 0 | 0 | 50 | 550 | +150 (+37.5%) |', rendered)
            self.assertNotIn('> Note: Gemini usage not measured', rendered)

    def test_agy_not_measured_when_source_dir_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing_agy = root / 'nonexistent_brain'
            stats = token_records.aggregate_weekly_tokens(
                days=7,
                agy_dir=missing_agy,
            )
            self.assertEqual(stats['per_cli']['agy']['status'], 'not measured')
            self.assertEqual(stats['per_cli']['agy']['change'], 'not measured')
            lines = token_records.token_report_lines(stats)
            rendered = '\n'.join(lines)
            self.assertIn('| agy (Antigravity) | not measured |', rendered)
            self.assertIn('> Note: agy usage not measured (missing source:', rendered)

    def test_claude_master_qwen_labour_combination_synthetic(self):
        """Report the Claude-master + Qwen-labour combination as its own counted row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            claude_dir = root / 'claude'
            qwen_dir = root / 'qwen'
            fm_dir = root / 'fm_state'
            for d in (claude_dir, qwen_dir, fm_dir):
                d.mkdir()

            now_dt = datetime(2026, 10, 25, 13, 0, 0, tzinfo=timezone.utc)
            now_ts = now_dt.timestamp()
            task_ts = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc).timestamp()

            # Create fleet-ledger entry
            ledger_file = fm_dir / 'fleet-ledger.jsonl'
            ledger_file.write_text(
                json.dumps({'task': 'task-claude-qwen', 'ts': task_ts}) + '\n',
                encoding='utf-8'
            )

            # Create Firstmate task meta for Claude worker
            wt_path = '/home/user/.treehouse/task-claude-qwen'
            meta_text = '\n'.join([
                'harness=claude',
                'model=claude-3-7-sonnet',
                f'worktree={wt_path}',
                f'spawn_gen=s{int(task_ts)}',
                '',
            ])
            (fm_dir / 'task-claude-qwen.meta').write_text(meta_text, encoding='utf-8')

            # Create Claude worker session in this treehouse worktree
            cl_proj = claude_dir / '-home-user--treehouse-task-claude-qwen'
            cl_proj.mkdir()
            (cl_proj / 'session.jsonl').write_text(
                json.dumps({
                    'timestamp': '2026-10-25T12:05:00Z',
                    'cwd': wt_path,
                    'sessionId': 'claude-worker-1',
                    'message': {
                        'model': 'claude-3-7-sonnet',
                        'usage': {'input_tokens': 2000, 'cache_read_input_tokens': 1000, 'output_tokens': 200},
                    },
                }) + '\n',
                encoding='utf-8'
            )

            # Create Qwen run in this worktree
            qwen_run = qwen_dir / '20261025T121000-qwenclaude'
            qwen_run.mkdir()
            (qwen_run / 'spec.json').write_text(json.dumps({'repo': wt_path}), encoding='utf-8')
            (qwen_run / 'verdict.json').write_text(
                json.dumps({'prompt_tokens': 500, 'completion_tokens': 100}),
                encoding='utf-8'
            )

            stats = token_records.aggregate_weekly_tokens(
                days=7,
                now=now_ts,
                claude_dir=claude_dir,
                qwen_dir=qwen_dir,
                fm_state_dir=fm_dir,
                fleet_ledger_path=ledger_file,
            )

            # Verify Claude-master + Qwen-labour combination exists
            self.assertIn(('Claude-master + Qwen-labour', 'claude-3-7-sonnet'), stats['combinations'])
            comb = stats['combinations'][('Claude-master + Qwen-labour', 'claude-3-7-sonnet')]
            self.assertEqual(comb['configuration'], 'Claude-master + Qwen-labour')
            self.assertEqual(comb['model'], 'claude-3-7-sonnet')
            self.assertEqual(comb['tasks'], 1)
            self.assertEqual(comb['master_tokens'], 3200)
            self.assertEqual(comb['labour_tokens'], 600)
            self.assertEqual(comb['total_tokens'], 3800)

            # Verify Markdown report formatting
            lines = token_records.token_report_lines(stats)
            rendered = '\n'.join(lines)
            self.assertIn('| Configuration | Model | Tasks | Master tokens | Labour tokens | Total |', rendered)
            self.assertIn('| Claude-master + Qwen-labour | claude-3-7-sonnet | 1 | 3,200 | 600 | 3,800 |', rendered)

    def test_new_concepts_this_week_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            concepts_file = Path(tmpdir) / 'concepts.md'
            concepts_file.write_text(
                "# Concepts glossary\n\n"
                "## Fast Speculation (2026-10-25)\n"
                "Speculation predicts future tokens early. It runs in parallel and saves time.\n\n"
                "## Old Technique (2026-10-01)\n"
                "This technique is from last month. It should not appear.\n",
                encoding='utf-8'
            )

            now_ts = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
            lines = token_records.new_concepts_lines(concepts_file, days=7, now=now_ts)
            rendered = '\n'.join(lines)

            self.assertIn('## New concepts this week', rendered)
            self.assertIn('- **Fast Speculation (2026-10-25)**: Speculation predicts future tokens early.', rendered)
            self.assertNotIn('Old Technique', rendered)
            self.assertIn('Have an idea or a better setup? Suggestions are welcome and route directly to proposals.jsonl.', rendered)

    def test_weekly_reset_deletes_records_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tokens_file = Path(tmpdir) / 'tokens.jsonl'
            tokens_file.write_text('{"cli": "claude", "total": 100}\n', encoding='utf-8')
            self.assertTrue(tokens_file.exists())
            token_records.reset_weekly_token_records(tokens_file)
            self.assertFalse(tokens_file.exists())


class TestSiIntegration(unittest.TestCase):
    def test_si_tokens_cli_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            code = si.main(['tokens', '--dry-run', '--days', '7'])
            self.assertEqual(code, 0)

    def test_si_tokens_cli_with_prev_totals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / 'prev.json'
            p.write_text('{"totals": {"claude": 100}}\n', encoding='utf-8')
            code = si.main(['tokens', '--dry-run', '--days', '7', '--prev-totals', str(p)])
            self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
