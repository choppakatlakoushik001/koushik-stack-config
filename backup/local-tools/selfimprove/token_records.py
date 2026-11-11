"""Weekly token-consumption tracking and concepts reporting for the self-improvement loop.

Privacy guarantee:
Extracts only numeric usage fields, timestamps, model names, session IDs,
and repo/worktree paths. Never copies, prints, stores, logs, or transmits message text.
Never accesses credential, auth, or secret files.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WEIGHT_INPUT = 1.0
WEIGHT_CACHE_READ = 0.1
WEIGHT_CACHE_WRITE = 1.25
WEIGHT_OUTPUT = 5.0

DEFAULT_CLAUDE_DIR = Path.home() / '.claude' / 'projects'
DEFAULT_CODEX_DIR = Path.home() / '.codex' / 'sessions'
DEFAULT_AGY_DIR = Path.home() / '.gemini' / 'antigravity-cli' / 'brain'
DEFAULT_GEMINI_DIR = Path.home() / '.gemini'
DEFAULT_QWEN_DIR = Path.home() / 'local-llm' / 'driver' / 'results'
DEFAULT_FM_STATE = Path('/home/chopp/kunchen/crew/kunchen_firstmate/state')
DEFAULT_FLEET_LEDGER = Path('/home/chopp/kunchen/crew/kunchen_firstmate/state/fleet-ledger.jsonl')
DEFAULT_CONCEPTS_PATH = Path('/home/chopp/kunchen/crew/kunchen_firstmate/data/concepts.md')
DEFAULT_PREV_TOTALS_PATH = Path(__file__).resolve().parent / 'tokens_previous.json'


def parse_iso_ts(ts_str: Optional[str]) -> Optional[float]:
    """Parse an ISO 8601 timestamp string to a UTC epoch timestamp."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        clean = ts_str.strip().replace('Z', '+00:00')
        return datetime.fromisoformat(clean).timestamp()
    except Exception:
        return None


def calculate_weighted_cost(
    input_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
    output: int = 0,
) -> float:
    """Calculate weighted token cost according to the standing cost formula:
    cache read ~0.1x input, cache write ~1.25x, output ~5x.
    """
    return round(
        (input_tokens * WEIGHT_INPUT)
        + (cache_read * WEIGHT_CACHE_READ)
        + (cache_write * WEIGHT_CACHE_WRITE)
        + (output * WEIGHT_OUTPUT),
        2,
    )


def load_previous_token_totals(path: Optional[Path] = None) -> Dict[str, Optional[int]]:
    """Load per-tool token totals from the previous week's summary file."""
    p = Path(path) if path else DEFAULT_PREV_TOTALS_PATH
    if not p.exists():
        return {}
    try:
        with open(p, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            if 'totals' in data and isinstance(data['totals'], dict):
                data = data['totals']
            res: Dict[str, Optional[int]] = {}
            for k, v in data.items():
                if isinstance(v, (int, float)):
                    res[k] = int(v)
                elif isinstance(v, dict) and 'total' in v and isinstance(v['total'], (int, float)):
                    res[k] = int(v['total'])
                elif v is None:
                    res[k] = None
            return res
    except Exception:
        return {}
    return {}


def save_previous_token_totals(totals: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Save per-tool token totals from the current week to preserve them across weekly reset."""
    p = Path(path) if path else DEFAULT_PREV_TOTALS_PATH
    clean_totals: Dict[str, Optional[int]] = {}
    for k, v in totals.items():
        if isinstance(v, (int, float)):
            clean_totals[k] = int(v)
        elif isinstance(v, dict):
            if v.get('status') in ('not measured', 'disabled'):
                clean_totals[k] = None
            elif 'total' in v and isinstance(v['total'], (int, float)):
                clean_totals[k] = int(v['total'])
            else:
                clean_totals[k] = None
        elif v is None:
            clean_totals[k] = None

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        temp_file = p.with_suffix('.tmp')
        payload = {
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'totals': clean_totals,
        }
        temp_file.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
        temp_file.replace(p)
    except OSError:
        pass


def format_tool_change(
    current: Optional[int],
    previous: Optional[int],
) -> Tuple[Optional[int], Optional[float], str]:
    """Calculate and format per-tool absolute and percentage change versus previous week.
    Returns (abs_change, pct_change, formatted_plain_text).
    """
    if current is None:
        return None, None, 'not measured'
    if previous is None:
        return None, None, 'N/A (no baseline)'

    diff = current - previous
    if previous == 0:
        if current == 0:
            return 0, 0.0, '+0 (+0.0%)'
        return diff, 100.0, f"+{diff:,} (+100.0%)"

    pct = round((diff / previous) * 100.0, 1)
    formatted = f"{diff:+,} ({pct:+.1f}%)"
    return diff, pct, formatted


def is_treehouse_worktree(cwd: Optional[str]) -> bool:
    """Check if a working directory belongs to an isolated worker worktree under ~/.treehouse/."""
    if not cwd or not isinstance(cwd, str):
        return False
    return '.treehouse' in cwd


def stream_claude_records(
    projects_dir: Optional[Path] = None,
    start_epoch: float = 0.0,
    end_epoch: float = float('inf'),
) -> List[Dict[str, Any]]:
    """Stream Claude usage from projects JSONL files without loading full files into memory."""
    p_dir = Path(projects_dir) if projects_dir else DEFAULT_CLAUDE_DIR
    records: List[Dict[str, Any]] = []
    if not p_dir.exists():
        return records

    for jsonl_file in p_dir.glob('*/*.jsonl'):
        try:
            with open(jsonl_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if 'usage' not in line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    msg = data.get('message')
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get('usage')
                    if not isinstance(usage, dict):
                        continue

                    ts_val = parse_iso_ts(data.get('timestamp'))
                    if ts_val is None:
                        ts_val = jsonl_file.stat().st_mtime
                    if not (start_epoch <= ts_val <= end_epoch):
                        continue

                    inp = int(usage.get('input_tokens', 0) or 0)
                    cr = int(usage.get('cache_read_input_tokens', 0) or 0)
                    cw = int(usage.get('cache_creation_input_tokens', 0) or 0)
                    out = int(usage.get('output_tokens', 0) or 0)
                    cwd = data.get('cwd')
                    sess_id = data.get('sessionId') or data.get('session_id') or jsonl_file.stem
                    model = msg.get('model') or data.get('requestedModel') or 'claude'

                    records.append({
                        'cli': 'claude',
                        'timestamp': ts_val,
                        'input': inp,
                        'cache_read': cr,
                        'cache_write': cw,
                        'output': out,
                        'total': inp + cr + cw + out,
                        'weighted': calculate_weighted_cost(inp, cr, cw, out),
                        'cwd': cwd,
                        'session_id': str(sess_id),
                        'model': str(model),
                    })
        except Exception:
            continue
    return records


def stream_codex_records(
    sessions_dir: Optional[Path] = None,
    start_epoch: float = 0.0,
    end_epoch: float = float('inf'),
) -> List[Dict[str, Any]]:
    """Stream Codex token_usage_record events without loading whole files into memory."""
    s_dir = Path(sessions_dir) if sessions_dir else DEFAULT_CODEX_DIR
    records: List[Dict[str, Any]] = []
    if not s_dir.exists():
        return records

    for jsonl_file in s_dir.glob('**/*.jsonl'):
        current_cwd = None
        current_sess_id = jsonl_file.stem
        try:
            with open(jsonl_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if 'token_usage_record' not in line and 'session_meta' not in line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    ev_type = data.get('type')
                    pld = data.get('payload', {})
                    if not isinstance(pld, dict):
                        continue

                    if ev_type == 'session_meta':
                        if pld.get('cwd'):
                            current_cwd = pld.get('cwd')
                        if pld.get('session_id') or pld.get('id'):
                            current_sess_id = pld.get('session_id') or pld.get('id')
                        continue

                    if ev_type == 'token_usage_record':
                        ts_val = parse_iso_ts(data.get('timestamp'))
                        if ts_val is None:
                            ts_val = jsonl_file.stat().st_mtime
                        if not (start_epoch <= ts_val <= end_epoch):
                            continue

                        usage = pld.get('usage', {})
                        if not isinstance(usage, dict):
                            continue

                        inp = int(usage.get('input_tokens', 0) or 0)
                        cr = int(usage.get('cached_input_tokens', 0) or 0)
                        cw = int(usage.get('cache_write_input_tokens', 0) or 0)
                        out = int(usage.get('output_tokens', 0) or 0)
                        sess_id = pld.get('session_id') or current_sess_id

                        records.append({
                            'cli': 'codex',
                            'timestamp': ts_val,
                            'input': inp,
                            'cache_read': cr,
                            'cache_write': cw,
                            'output': out,
                            'total': inp + cr + cw + out,
                            'weighted': calculate_weighted_cost(inp, cr, cw, out),
                            'cwd': current_cwd,
                            'session_id': str(sess_id),
                            'model': 'codex',
                        })
        except Exception:
            continue
    return records


def stream_agy_records(
    brain_dir: Optional[Path] = None,
    start_epoch: float = 0.0,
    end_epoch: float = float('inf'),
) -> List[Dict[str, Any]]:
    """Stream Antigravity (agy) transcript events without loading whole files into memory."""
    b_dir = Path(brain_dir) if brain_dir else DEFAULT_AGY_DIR
    records: List[Dict[str, Any]] = []
    if not b_dir.exists():
        return records

    for transcript_file in b_dir.glob('*/.system_generated/logs/transcript*.jsonl'):
        sess_id = transcript_file.parent.parent.parent.name
        detected_cwd = None
        try:
            with open(transcript_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if 'input_tokens' not in line and 'CommandLine' not in line and 'Path' not in line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue

                    # Try to discover cwd from early tool calls if not yet detected
                    if not detected_cwd and 'tool_calls' in data:
                        for tc in data.get('tool_calls', []):
                            args = tc.get('function', {}).get('arguments') or tc.get('args') or {}
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    pass
                            if isinstance(args, dict):
                                cmd = str(args.get('CommandLine', ''))
                                p_arg = str(args.get('Path', ''))
                                m = re.search(r'(?:cd|git -C)\s+([^\s;&)]+)', cmd)
                                if m:
                                    detected_cwd = m.group(1).strip('\'"')
                                    break
                                m = re.search(r'(/[^\s\'"<>]+/\.treehouse/[^\s\'"<>]+)', cmd + ' ' + p_arg)
                                if m:
                                    detected_cwd = m.group(1).strip('\'"')
                                    break

                    if data.get('type') != 'PLANNER_RESPONSE':
                        continue

                    ts_val = parse_iso_ts(data.get('created_at'))
                    if ts_val is None:
                        ts_val = transcript_file.stat().st_mtime
                    if not (start_epoch <= ts_val <= end_epoch):
                        continue

                    inp = int(data.get('input_tokens', 0) or 0)
                    cr = int(data.get('cache_read_tokens', 0) or 0)
                    cw = 0
                    out = int(data.get('output_tokens', 0) or 0)

                    records.append({
                        'cli': 'agy',
                        'timestamp': ts_val,
                        'input': inp,
                        'cache_read': cr,
                        'cache_write': cw,
                        'output': out,
                        'total': inp + cr + cw + out,
                        'weighted': calculate_weighted_cost(inp, cr, cw, out),
                        'cwd': detected_cwd,
                        'session_id': str(sess_id),
                        'model': 'gemini-high',
                    })
        except Exception:
            continue
    return records


def stream_gemini_records(
    gemini_dir: Optional[Path] = None,
    start_epoch: float = 0.0,
    end_epoch: float = float('inf'),
) -> List[Dict[str, Any]]:
    """Stream Gemini CLI records if any exist under ~/.gemini (excluding antigravity-cli).
    Returns an empty list if no token-bearing logs exist.
    """
    g_dir = Path(gemini_dir) if gemini_dir else DEFAULT_GEMINI_DIR
    records: List[Dict[str, Any]] = []
    if not g_dir.exists():
        return records

    for log_file in g_dir.glob('**/*.jsonl'):
        if 'antigravity-cli' in log_file.parts:
            continue
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if 'token' not in line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    if 'input_tokens' in data or 'prompt_tokens' in data:
                        inp = int(data.get('input_tokens') or data.get('prompt_tokens') or 0)
                        out = int(data.get('output_tokens') or data.get('completion_tokens') or 0)
                        cr = int(data.get('cache_read_tokens', 0) or 0)
                        cw = int(data.get('cache_write_tokens', 0) or 0)
                        ts_val = parse_iso_ts(data.get('timestamp') or data.get('created_at'))
                        if ts_val is None:
                            ts_val = log_file.stat().st_mtime
                        if start_epoch <= ts_val <= end_epoch:
                            records.append({
                                'cli': 'gemini',
                                'timestamp': ts_val,
                                'input': inp,
                                'cache_read': cr,
                                'cache_write': cw,
                                'output': out,
                                'total': inp + cr + cw + out,
                                'weighted': calculate_weighted_cost(inp, cr, cw, out),
                                'cwd': data.get('cwd'),
                                'session_id': log_file.stem,
                                'model': 'gemini',
                            })
        except Exception:
            continue
    return records


def stream_qwen_records(
    results_dir: Optional[Path] = None,
    start_epoch: float = 0.0,
    end_epoch: float = float('inf'),
) -> List[Dict[str, Any]]:
    """Stream Qwen local driver results without loading multiple files into memory.
    Uses top-level prompt_tokens and completion_tokens fields when present, jobs[] only as fallback.
    """
    r_dir = Path(results_dir) if results_dir else DEFAULT_QWEN_DIR
    records: List[Dict[str, Any]] = []
    if not r_dir.exists():
        return records

    for verdict_file in r_dir.glob('*/verdict.json'):
        try:
            run_dir = verdict_file.parent
            ts_val = None
            m = re.match(r'^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})', run_dir.name)
            if m:
                dt = datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    int(m.group(4)), int(m.group(5)), int(m.group(6)),
                    tzinfo=timezone.utc,
                )
                ts_val = dt.timestamp()
            if ts_val is None:
                ts_val = verdict_file.stat().st_mtime

            if not (start_epoch <= ts_val <= end_epoch):
                continue

            with open(verdict_file, 'r', encoding='utf-8', errors='ignore') as f:
                data = json.load(f)

            repo = data.get('repo')
            if not repo:
                spec_file = run_dir / 'spec.json'
                if spec_file.exists():
                    try:
                        with open(spec_file, 'r', encoding='utf-8', errors='ignore') as sf:
                            spec_data = json.load(sf)
                            repo = spec_data.get('repo')
                    except Exception:
                        pass

            jobs = data.get('jobs', [])
            total_prompt = 0
            total_completion = 0
            has_completion = False

            # (1) Use top-level fields when present, jobs[] only as fallback
            if 'prompt_tokens' in data or 'completion_tokens' in data:
                total_prompt = int(data.get('prompt_tokens', 0) or 0)
                if data.get('completion_tokens') is not None:
                    total_completion = int(data['completion_tokens'])
                    has_completion = True
                elif jobs and isinstance(jobs, list):
                    for j in jobs:
                        if isinstance(j, dict) and j.get('completion_tokens') is not None:
                            total_completion += int(j['completion_tokens'])
                            has_completion = True
            elif jobs and isinstance(jobs, list):
                for j in jobs:
                    if isinstance(j, dict):
                        total_prompt += int(j.get('prompt_tokens', 0) or 0)
                        if j.get('completion_tokens') is not None:
                            total_completion += int(j['completion_tokens'])
                            has_completion = True

            records.append({
                'cli': 'qwen',
                'timestamp': ts_val,
                'input': total_prompt,
                'cache_read': 0,
                'cache_write': 0,
                'output': total_completion if has_completion else None,
                'total': total_prompt + (total_completion if has_completion else 0),
                'weighted': calculate_weighted_cost(total_prompt, 0, 0, total_completion if has_completion else 0),
                'cwd': repo,
                'session_id': run_dir.name,
                'model': 'qwen',
            })
        except Exception:
            continue
    return records


def load_fleet_ledger(ledger_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load task names and timestamps from fleet-ledger.jsonl.
    PRIVACY: Reads fields task and ts only - never reads its text field.
    """
    path = Path(ledger_path) if ledger_path else DEFAULT_FLEET_LEDGER
    entries: List[Dict[str, Any]] = []
    if not path.exists():
        return entries
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if 'task' not in line:
                    continue
                try:
                    data = json.loads(line)
                    t_val = data.get('task')
                    ts_val = data.get('ts')
                    if t_val and ts_val is not None:
                        entries.append({'task': str(t_val), 'ts': float(ts_val)})
                except Exception:
                    continue
    except Exception:
        pass
    return entries


def load_firstmate_meta_hints(state_dir: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Read available *.meta files under firstmate state/ as an extra hint for task info."""
    s_dir = Path(state_dir) if state_dir else DEFAULT_FM_STATE
    hints: Dict[str, Dict[str, str]] = {}
    if not s_dir.exists():
        return hints

    for meta_file in s_dir.glob('*.meta'):
        try:
            content = meta_file.read_text(encoding='utf-8', errors='ignore')
            meta_dict: Dict[str, str] = {'task_id': meta_file.stem}
            for line in content.splitlines():
                if '=' in line:
                    k, v = line.split('=', 1)
                    meta_dict[k.strip()] = v.strip()
            wt = meta_dict.get('worktree')
            if wt:
                hints[wt] = meta_dict
            hints[meta_file.stem] = meta_dict
        except Exception:
            continue
    return hints


def aggregate_weekly_tokens(
    days: int = 7,
    now: Optional[float] = None,
    claude_dir: Optional[Path] = None,
    codex_dir: Optional[Path] = None,
    agy_dir: Optional[Path] = None,
    gemini_dir: Optional[Path] = None,
    qwen_dir: Optional[Path] = None,
    fm_state_dir: Optional[Path] = None,
    fleet_ledger_path: Optional[Path] = None,
    prev_totals_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Aggregate per-CLI totals and master/labour combination breakdowns for the week."""
    cur_epoch = now if now is not None else datetime.now(timezone.utc).timestamp()
    start_epoch = cur_epoch - (days * 86400)

    claude_recs = stream_claude_records(claude_dir, start_epoch, cur_epoch)
    codex_recs = stream_codex_records(codex_dir, start_epoch, cur_epoch)
    agy_recs = stream_agy_records(agy_dir, start_epoch, cur_epoch)
    gemini_recs = stream_gemini_records(gemini_dir, start_epoch, cur_epoch)
    qwen_recs = stream_qwen_records(qwen_dir, start_epoch, cur_epoch)

    fleet_ledger = load_fleet_ledger(fleet_ledger_path)
    meta_hints = load_firstmate_meta_hints(fm_state_dir)

    p_claude = Path(claude_dir) if claude_dir else DEFAULT_CLAUDE_DIR
    p_codex = Path(codex_dir) if codex_dir else DEFAULT_CODEX_DIR
    p_agy = Path(agy_dir) if agy_dir else DEFAULT_AGY_DIR
    p_gemini = Path(gemini_dir) if gemini_dir else DEFAULT_GEMINI_DIR
    p_qwen = Path(qwen_dir) if qwen_dir else DEFAULT_QWEN_DIR

    # 1. Per-CLI weekly totals
    per_cli = {
        'claude': {'input': 0, 'cache_read': 0, 'cache_write': 0, 'output': 0, 'total': 0, 'count': 0, 'status': 'active'},
        'codex': {'input': 0, 'cache_read': 0, 'cache_write': 0, 'output': 0, 'total': 0, 'count': 0, 'status': 'active'},
        'agy': {'input': 0, 'cache_read': 0, 'cache_write': 0, 'output': 0, 'total': 0, 'count': 0, 'status': 'active'},
        'gemini': {'status': 'not measured', 'input': 0, 'cache_read': 0, 'cache_write': 0, 'output': 0, 'total': 0, 'count': 0},
        'qwen': {'prompt': 0, 'completion': 0, 'has_completion': False, 'total': 0, 'count': 0, 'status': 'active'},
        'grok': {'status': 'disabled'},
    }

    if not p_claude.exists():
        per_cli['claude']['status'] = 'not measured'
        per_cli['claude']['missing_source'] = f"{p_claude} (directory not found)"

    if not p_codex.exists():
        per_cli['codex']['status'] = 'not measured'
        per_cli['codex']['missing_source'] = f"{p_codex} (directory not found)"

    if not p_agy.exists():
        per_cli['agy']['status'] = 'not measured'
        per_cli['agy']['missing_source'] = f"{p_agy} (directory not found)"

    if not p_qwen.exists():
        per_cli['qwen']['status'] = 'not measured'
        per_cli['qwen']['missing_source'] = f"{p_qwen} (directory not found)"

    for r in claude_recs:
        per_cli['claude']['input'] += r['input']
        per_cli['claude']['cache_read'] += r['cache_read']
        per_cli['claude']['cache_write'] += r['cache_write']
        per_cli['claude']['output'] += r['output']
        per_cli['claude']['total'] += r['total']
        per_cli['claude']['count'] += 1

    for r in codex_recs:
        per_cli['codex']['input'] += r['input']
        per_cli['codex']['cache_read'] += r['cache_read']
        per_cli['codex']['cache_write'] += r['cache_write']
        per_cli['codex']['output'] += r['output']
        per_cli['codex']['total'] += r['total']
        per_cli['codex']['count'] += 1

    for r in agy_recs:
        per_cli['agy']['input'] += r['input']
        per_cli['agy']['cache_read'] += r['cache_read']
        per_cli['agy']['cache_write'] += r['cache_write']
        per_cli['agy']['output'] += r['output']
        per_cli['agy']['total'] += r['total']
        per_cli['agy']['count'] += 1

    if gemini_recs:
        per_cli['gemini']['status'] = 'active'
        for r in gemini_recs:
            per_cli['gemini']['input'] += r['input']
            per_cli['gemini']['cache_read'] += r['cache_read']
            per_cli['gemini']['cache_write'] += r['cache_write']
            per_cli['gemini']['output'] += r['output']
            per_cli['gemini']['total'] += r['total']
            per_cli['gemini']['count'] += 1
    else:
        per_cli['gemini']['status'] = 'not measured'
        per_cli['gemini']['missing_source'] = f"{p_gemini} (no token logs found)"

    for r in qwen_recs:
        per_cli['qwen']['prompt'] += r['input']
        if r['output'] is not None:
            per_cli['qwen']['completion'] += r['output']
            per_cli['qwen']['has_completion'] = True
        per_cli['qwen']['total'] += r['total']
        per_cli['qwen']['count'] += 1

    for c in ('claude', 'codex', 'agy'):
        per_cli[c]['weighted'] = calculate_weighted_cost(
            per_cli[c]['input'],
            per_cli[c]['cache_read'],
            per_cli[c]['cache_write'],
            per_cli[c]['output'],
        )

    if per_cli['gemini']['status'] == 'active':
        per_cli['gemini']['weighted'] = calculate_weighted_cost(
            per_cli['gemini']['input'],
            per_cli['gemini']['cache_read'],
            per_cli['gemini']['cache_write'],
            per_cli['gemini']['output'],
        )
    else:
        per_cli['gemini']['weighted'] = 0.0

    per_cli['qwen']['weighted'] = calculate_weighted_cost(
        per_cli['qwen']['prompt'],
        0,
        0,
        per_cli['qwen']['completion'],
    )

    # Calculate trends vs previous week
    prev_totals = load_previous_token_totals(prev_totals_path)
    for cli in ('claude', 'codex', 'agy', 'gemini', 'qwen', 'grok'):
        c = per_cli[cli]
        if c.get('status') == 'not measured':
            c['change'] = 'not measured'
            c['prev_total'] = prev_totals.get(cli)
        elif c.get('status') == 'disabled':
            c['change'] = 'N/A'
            c['prev_total'] = None
        else:
            cur_val = c.get('total', 0)
            prev_val = prev_totals.get(cli)
            c['prev_total'] = prev_val
            _, _, change_str = format_tool_change(cur_val, prev_val)
            c['change'] = change_str

    # 2. Worker task attribution: each CLI session whose cwd is under ~/.treehouse/ is a worker task.
    # Group CLI records by session
    sessions: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in (*claude_recs, *codex_recs, *agy_recs, *gemini_recs):
        key = (r['cli'], r['session_id'])
        if key not in sessions:
            sessions[key] = {
                'cli': r['cli'],
                'session_id': r['session_id'],
                'cwd': r.get('cwd'),
                'model': r.get('model', 'default'),
                'min_ts': r['timestamp'],
                'max_ts': r['timestamp'],
                'total_tokens': 0,
                'records': [],
            }
        s = sessions[key]
        if r.get('cwd') and not s['cwd']:
            s['cwd'] = r['cwd']
        s['min_ts'] = min(s['min_ts'], r['timestamp'])
        s['max_ts'] = max(s['max_ts'], r['timestamp'])
        s['total_tokens'] += r['total']
        s['records'].append(r)

    # Separate worker tasks from non-worker sessions
    worker_tasks: List[Dict[str, Any]] = []
    unattributed_records: List[Dict[str, Any]] = []

    for s in sessions.values():
        if is_treehouse_worktree(s['cwd']):
            worker_tasks.append(s)
        else:
            unattributed_records.extend(s['records'])

    for qr in qwen_recs:
        qr['matched_task'] = None

    # Track combinations: (combination_label, model) -> stats
    combinations: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for wt in worker_tasks:
        s_cwd = wt['cwd']
        s_ts = wt['min_ts']
        cli_name = wt['cli']

        # Task name resolution: fleet-ledger -> .meta -> cwd basename
        task_name = None
        min_dist = float('inf')
        for le in fleet_ledger:
            dist = abs(le['ts'] - s_ts)
            if dist <= 7200 and dist < min_dist:
                min_dist = dist
                task_name = le['task']

        meta_info = meta_hints.get(s_cwd) if s_cwd else None
        if not task_name and meta_info:
            task_name = meta_info.get('task_id')
        if not task_name and s_cwd:
            task_name = Path(s_cwd).name

        if (not cli_name or cli_name in ('default', 'unknown')) and meta_info and meta_info.get('harness'):
            cli_name = meta_info['harness']

        # Model resolution: .meta hint -> session model -> default
        model_name = (meta_info.get('model') if meta_info else None) or wt.get('model') or 'default'

        # Match Qwen driver runs whose repo matches this task's worktree inside time window
        matched_qwen_tokens = 0
        for qr in qwen_recs:
            if qr.get('matched_task') is not None:
                continue
            qr_cwd = qr.get('cwd')
            if not qr_cwd:
                continue
            if qr_cwd == s_cwd or (s_cwd and (s_cwd in qr_cwd or qr_cwd in s_cwd)):
                if abs(qr['timestamp'] - s_ts) <= 7200 or (wt['min_ts'] - 300 <= qr['timestamp'] <= wt['max_ts'] + 300):
                    qr['matched_task'] = task_name or s_cwd
                    matched_qwen_tokens += qr['total']

        # (3) Combination label: master CLI + labour CLI (e.g. agy+Qwen, Claude-master + Qwen-labour, or alone)
        master_cli_label = {'agy': 'agy', 'claude': 'Claude', 'codex': 'Codex'}.get(cli_name, cli_name)
        if matched_qwen_tokens > 0:
            if str(cli_name).lower() == 'claude' or str(master_cli_label).lower() == 'claude':
                comb_label = "Claude-master + Qwen-labour"
            else:
                comb_label = f"{master_cli_label}+Qwen"
        else:
            comb_label = f"{master_cli_label} alone"

        comb_key = (comb_label, model_name)
        if comb_key not in combinations:
            combinations[comb_key] = {
                'configuration': comb_label,
                'model': model_name,
                'tasks': 0,
                'master_tokens': 0,
                'labour_tokens': 0,
                'total_tokens': 0,
            }
        combinations[comb_key]['tasks'] += 1
        combinations[comb_key]['master_tokens'] += wt['total_tokens']
        combinations[comb_key]['labour_tokens'] += matched_qwen_tokens
        combinations[comb_key]['total_tokens'] += wt['total_tokens'] + matched_qwen_tokens

    # Unmatched Qwen runs go to unattributed
    for qr in qwen_recs:
        if qr.get('matched_task') is None:
            unattributed_records.append(qr)

    # 3. Unattributed usage per CLI
    unattributed: Dict[str, Dict[str, Any]] = {
        'claude': {'total': 0, 'weighted': 0.0},
        'codex': {'total': 0, 'weighted': 0.0},
        'agy': {'total': 0, 'weighted': 0.0},
        'gemini': {'total': 0, 'weighted': 0.0, 'status': per_cli['gemini']['status']},
        'qwen': {'total': 0, 'weighted': 0.0},
    }

    for r in unattributed_records:
        cli = r['cli']
        if cli in unattributed:
            unattributed[cli]['total'] += r['total']
            unattributed[cli]['weighted'] = round(
                unattributed[cli]['weighted'] + r['weighted'], 2
            )

    all_records = [*claude_recs, *codex_recs, *agy_recs, *gemini_recs, *qwen_recs]

    return {
        'days': days,
        'per_cli': per_cli,
        'previous_totals': prev_totals,
        'combinations': combinations,
        'unattributed': unattributed,
        'all_records': all_records,
    }


def token_report_lines(stats: Dict[str, Any]) -> List[str]:
    """Generate Markdown report lines for weekly token consumption."""
    lines = [
        '',
        '## Weekly token consumption',
        '',
        '### Per-CLI totals',
        '',
        '| CLI | Status | Input / Prompt | Cache read | Cache write | Output / Completion | Total | Change vs last week | Weighted cost |',
        '|---|---|---|---|---|---|---|---|---|',
    ]

    pcli = stats['per_cli']
    for cli, label in (('claude', 'Claude'), ('codex', 'Codex'), ('agy', 'agy (Antigravity)')):
        c = pcli[cli]
        if c.get('status') == 'not measured':
            lines.append(
                f"| {label} | not measured | not measured | not measured | not measured | not measured | not measured | not measured | not measured |"
            )
        else:
            lines.append(
                f"| {label} | active | {c['input']:,} | {c['cache_read']:,} | {c['cache_write']:,} | {c['output']:,} | {c['total']:,} | {c.get('change', 'N/A')} | {c['weighted']:,.2f} |"
            )

    # Gemini
    gem = pcli['gemini']
    if gem.get('status') in ('not measured', 'unknown'):
        lines.append(
            '| Gemini | not measured | not measured | not measured | not measured | not measured | not measured | not measured | not measured |'
        )
    else:
        lines.append(
            f"| Gemini | active | {gem['input']:,} | {gem['cache_read']:,} | {gem['cache_write']:,} | {gem['output']:,} | {gem['total']:,} | {gem.get('change', 'N/A')} | {gem['weighted']:,.2f} |"
        )

    # Qwen (local)
    qw = pcli['qwen']
    if qw.get('status') == 'not measured':
        lines.append(
            '| Qwen (local) | not measured | not measured | not measured | not measured | not measured | not measured | not measured | not measured |'
        )
    else:
        comp_str = f"{qw['completion']:,}" if qw['has_completion'] else 'unknown'
        lines.append(
            f"| Qwen (local) | active | {qw['prompt']:,} | N/A | N/A | {comp_str} | {qw['total']:,} | {qw.get('change', 'N/A')} | {qw['weighted']:,.2f} |"
        )

    # Grok
    lines.append('| Grok | disabled | N/A | N/A | N/A | N/A | N/A | N/A | N/A |')

    # Missing sources notes
    missing_notes = []
    for cli, label in (
        ('gemini', 'Gemini'),
        ('agy', 'agy'),
        ('claude', 'Claude'),
        ('codex', 'Codex'),
        ('qwen', 'Qwen'),
    ):
        c = pcli.get(cli, {})
        if c.get('status') == 'not measured' and c.get('missing_source'):
            missing_notes.append(f"> Note: {label} usage not measured (missing source: {c['missing_source']}).")
    if missing_notes:
        lines.append('')
        lines.extend(missing_notes)

    lines.extend([
        '',
        '> Weighted cost formula: cache read ~0.1x input, cache write ~1.25x, output ~5x input.',
        '',
        '### Master and labour combinations',
        '',
    ])

    combs = stats.get('combinations', {})
    if combs:
        lines.extend([
            '| Configuration | Model | Tasks | Master tokens | Labour tokens | Total |',
            '|---|---|---|---|---|---|',
        ])
        for (comb_label, model_name), v in sorted(combs.items()):
            lines.append(
                f"| {comb_label} | {model_name} | {v['tasks']} | {v['master_tokens']:,} | {v['labour_tokens']:,} | {v['total_tokens']:,} |"
            )
    else:
        lines.append('No matched worker task combinations this week.')

    lines.extend([
        '',
        '### Unattributed usage',
        '',
        '| CLI | Unattributed tokens | Weighted cost |',
        '|---|---|---|',
    ])
    unattr = stats.get('unattributed', {})
    for cli, label in (
        ('claude', 'Claude (supervisor master)'),
        ('agy', 'agy'),
        ('codex', 'Codex'),
        ('gemini', 'Gemini'),
        ('qwen', 'Qwen'),
    ):
        u = unattr.get(cli, {})
        if u.get('status') in ('not measured', 'unknown'):
            lines.append(f"| {label} | not measured | not measured |")
        else:
            lines.append(f"| {label} | {u.get('total', 0):,} | {u.get('weighted', 0.0):,.2f} |")

    return lines


def new_concepts_lines(
    concepts_path: Optional[Path] = None,
    days: int = 7,
    now: Optional[float] = None,
) -> List[str]:
    """Extract concepts added in the week from concepts.md (heading + first sentence)
    followed by an invitation for setups routing to proposals.jsonl.
    """
    c_path = Path(concepts_path) if concepts_path else DEFAULT_CONCEPTS_PATH
    cur_epoch = now if now is not None else datetime.now(timezone.utc).timestamp()
    start_epoch = cur_epoch - (days * 86400)

    lines = ['', '## New concepts this week', '']
    if not c_path.exists():
        lines.extend([
            'None this week.',
            '',
            'Have an idea or a better setup? Suggestions are welcome and route directly to proposals.jsonl.',
        ])
        return lines

    text = c_path.read_text(encoding='utf-8', errors='ignore')
    heading_pattern = re.compile(r'^##\s+(.+?)\s*\((\d{4}-\d{2}-\d{2})\)\s*$', re.M)

    matches = list(heading_pattern.finditer(text))
    weekly_concepts = []

    for i, m in enumerate(matches):
        title = m.group(1).strip()
        date_str = m.group(2).strip()
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
            if start_epoch <= ts <= cur_epoch:
                body_start = m.end()
                body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                body = text[body_start:body_end].strip()
                first_sentence = ''
                if body:
                    sm = re.search(r'^(.*?[.!?])(?:\s|\Z)', body, re.S)
                    if sm:
                        first_sentence = sm.group(1).strip().replace('\n', ' ')
                    else:
                        first_sentence = body.splitlines()[0].strip()
                weekly_concepts.append((title, date_str, first_sentence))
        except Exception:
            continue

    if weekly_concepts:
        for title, date_str, first_sentence in weekly_concepts:
            lines.append(f"- **{title} ({date_str})**: {first_sentence}")
    else:
        lines.append('None this week.')

    lines.extend([
        '',
        'Have an idea or a better setup? Suggestions are welcome and route directly to proposals.jsonl.',
    ])
    return lines


def reset_weekly_token_records(
    records_path: Path,
    prev_totals_path: Optional[Path] = None,
    current_totals: Optional[Dict[str, Any]] = None,
) -> None:
    """Save current week's totals to previous totals file so the comparison survives
    the weekly reset, then delete the week's token records.
    """
    if current_totals is not None:
        save_previous_token_totals(current_totals, prev_totals_path)
    elif records_path and Path(records_path).exists():
        totals: Dict[str, int] = {}
        try:
            with open(records_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        cli = r.get('cli')
                        tot = int(r.get('total', 0) or 0)
                        if cli:
                            totals[cli] = totals.get(cli, 0) + tot
                    except Exception:
                        continue
            if totals:
                save_previous_token_totals(totals, prev_totals_path)
        except Exception:
            pass

    try:
        p = Path(records_path)
        if p.exists():
            p.unlink()
    except OSError:
        pass
