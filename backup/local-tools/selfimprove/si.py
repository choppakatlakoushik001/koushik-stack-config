#!/usr/bin/env python3
"""Weekly self-improvement loop for the WSL setup (all CLIs and local models).

  si.py record --task ID --master M --labour L [--quota-before P --quota-after P]
               [--minutes N] [--checks PASSED/TOTAL] [--blocker TEXT]... --outcome done|failed
  si.py propose --idea TEXT --why TEXT [--gain TEXT --risk TEXT --check TEXT --source TEXT]
  si.py decide INDEX adopted|rejected|later [--note TEXT]
  si.py review [--days 7]      write reports/<date>.md with stats and proposals
  si.py tune                   evaluate/undo a trial, or start one approved step

Records and proposals are JSON lines in records.jsonl and proposals.jsonl
(append-only). Proposal indexes count proposal entries from 1, excluding decisions.
Decisions append a reference to that index; the last decision wins in review. The review
only proposes Tier 2 changes. Tier 1 may adjust an explicitly approved local
JSON setting within bounds, with frozen benchmark measurements and automatic
rollback. Shipped Qwen tunables have captain-approved local proposal settings;
they do not control live driver routing. Review runs tune before reporting;
it does not install anything or change scheduling.
"""
import argparse
import fcntl
import json
import hashlib
import math
import os
import re
import subprocess
import sys
import sqlite3
import time
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RECORDS = ROOT / 'records.jsonl'
PROPOSALS = ROOT / 'proposals.jsonl'
REPORTS = ROOT / 'reports'
TUNABLES = ROOT / 'tunables.json'
TUNING = ROOT / 'tuning.jsonl'
CHARTER = ROOT / 'CHARTER.md'
WATCHED = ROOT / 'watched.json'
ADVANCEMENTS = ROOT / 'advancements.jsonl'
FM = Path('/home/chopp/kunchen/crew/kunchen_firstmate')
FINDINGS_DEFAULT = Path.home() / 'local-llm' / 'leakscan' / 'findings.jsonl'
TOKENS = ROOT / 'tokens.jsonl'
PREV_TOKENS = ROOT / 'tokens_previous.json'
CONCEPTS_DEFAULT = FM / 'data' / 'concepts.md'
CONCEPTS = CONCEPTS_DEFAULT
GRAPH_DB = ROOT / 'graph.sqlite'
HINTS = ROOT / 'hints.jsonl'
DRIVER_RESULTS_DEFAULT = Path('/home/chopp/local-llm/driver/results')

PROMPT_TOKEN_BUCKETS = [
    (0, 500, '<500'),
    (500, 1000, '500-999'),
    (1000, 2000, '1000-1999'),
    (2000, None, '2000+'),
]

OUTPUT_LINE_BUCKETS = [
    (0, 20, '<20'),
    (20, 50, '20-49'),
    (50, 100, '50-99'),
    (100, None, '100+'),
]

import graph
import noop
import bgapps
from bgapps import bgapps_sweep_lines

from token_records import (
    aggregate_weekly_tokens,
    token_report_lines,
    new_concepts_lines,
    reset_weekly_token_records,
)

VALID_CHARTER_STATUSES = ('built', 'built-not-wired', 'proposed', 'gap', 'partial')


def parse_charter_status(status_str):
    status_str = status_str.strip()
    if not status_str:
        raise ValueError('empty charter status')
    token = status_str.split()[0].split('(')[0].strip()
    if token in VALID_CHARTER_STATUSES:
        return token
    if status_str.startswith('built-not-wired'):
        return 'built-not-wired'
    if status_str.startswith('built'):
        return 'built'
    raise ValueError(f"invalid charter status '{status_str}'")


def charter_rows(path=None):
    path = Path(path) if path else CHARTER
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    in_table = False
    rows = []
    for line in lines:
        if line.startswith('| # |'):
            in_table = True
            continue
        if in_table:
            if not line.startswith('|'):
                break
            if line.startswith('|---'):
                continue
            cols = [c.strip() for c in line.split('|')[1:-1]]
            if len(cols) >= 4:
                req_id, req_text, req_status, req_where = cols[:4]
                base_status = parse_charter_status(req_status)
                rows.append({
                    'id': req_id,
                    'requirement': req_text,
                    'status': req_status,
                    'base_status': base_status,
                    'where': req_where,
                })
    return rows


def charter_open_rows(path=None):
    return [r for r in charter_rows(path) if r['base_status'] != 'built']


def charter_gap_lines(path=None):
    rows = charter_open_rows(path)
    lines = ['', '## Charter gaps', '']
    if not rows:
        lines.append('All charter requirements are built.')
    else:
        for r in rows:
            where_str = f" ({r['where']})" if r['where'] else ''
            lines.append(f"- {r['id']}: {r['requirement']} [{r['status']}]{where_str}")
    return lines


SCORECARD_GOALS = [
    'advanced',
    'reliable',
    'trustworthy',
    'adaptable',
    'safe',
    'secure',
    'efficient/conservative',
    'throughput/experience',
]

SCORECARD_TOOLS = [
    'Claude',
    'Codex',
    'agy',
    'Gemini',
    'Grok',
    'Qwen',
    'Firstmate',
    'loop',
]


def normalize_goal(goal_str):
    if not goal_str:
        return None
    g = goal_str.strip().lower().replace(' and ', '/').replace(' ', '_')
    for std in SCORECARD_GOALS:
        if g == std or g == std.replace('/', '_') or g in std:
            return std
    return goal_str.strip()


def findings_path():
    env_path = os.environ.get('LEAKSCAN_FINDINGS')
    if env_path:
        return Path(env_path).expanduser()
    return FINDINGS_DEFAULT


def parse_finding_ts(at_val):
    if isinstance(at_val, (int, float)):
        return float(at_val)
    if isinstance(at_val, str):
        try:
            dt = datetime.fromisoformat(at_val.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0
    return 0.0


def is_test_fixture_path(path_str: str) -> bool:
    if not path_str or path_str == '-':
        return False
    p = str(path_str).lower()
    parts = Path(p).parts
    if any(part in ('tests', 'test', 'fixtures', 'test_fixtures', 'test-fixtures', 'benchmarks', 'bench', 'mock', 'mocks') for part in parts):
        return True
    if 'fixture' in p:
        return True
    name = Path(p).name
    if re.search(r'(?:^test_.*\.py$|_test\.py$|\.test\.(?:sh|ts|js|py)$|_test\.(?:sh|ts|js|py)$)', name):
        return True
    return False


def is_hex_or_class_shape(token: str) -> bool:
    if not token or not isinstance(token, str):
        return False
    t = token.strip()
    if re.fullmatch(r'[0-9a-fA-F]{7,64}', t):
        return True
    if re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', t):
        return True
    for p in ('sha256:', 'sha1:', 'md5:', 'sha512:', 'sha256-', 'sha512-', 'urn:'):
        if t.lower().startswith(p):
            rest = t[len(p):]
            if re.fullmatch(r'[0-9a-fA-F]+', rest):
                return True
    if re.fullmatch(r'[A-Za-z0-9_]+', t) and any(t.endswith(s) for s in ('Adapter', 'Config', 'Model', 'Handler', 'Zoo')):
        return True
    return False


def is_known_hash_or_checksum_finding(finding: dict) -> bool:
    rule = finding.get('rule', '')
    if rule != 'high-entropy-token':
        return False

    match = finding.get('match')
    if match and isinstance(match, str):
        # A matched token is only suppressed if it is hex-shaped or the known class-name shape.
        # Non-hex tokens (such as base64 or sk- style tokens) are NEVER suppressed,
        # even if the line mentions "commit" or is located in a .md file.
        if is_hex_or_class_shape(match):
            return True
        return False

    line_no = finding.get('line', 0)
    path = finding.get('path', '')
    if path and path != '-':
        p = Path(path)
        if not p.exists():
            fm_candidate = Path('/home/chopp/kunchen/crew/kunchen_firstmate') / path
            if fm_candidate.exists():
                p = fm_candidate
        if p.exists():
            try:
                with p.open('r', encoding='utf-8', errors='ignore') as f:
                    for idx, line in enumerate(f, 1):
                        if idx == line_no:
                            # Non-hex credential shapes on the line must never be suppressed
                            if re.search(r'\b(sk-[A-Za-z0-9_\-]{15,}|ghp_[A-Za-z0-9]{20,}|bearer\s+[A-Za-z0-9_\-\.]{20,})\b', line, re.I):
                                return False
                            if re.search(r'\b[0-9a-fA-F]{40}\b|\b[0-9a-fA-F]{64}\b', line):
                                return True
                            if re.search(r'\b(sha256|sha1|md5|hash|checksum|digest|commit|revision|fingerprint)\b', line, re.I):
                                if re.search(r'\b[0-9a-fA-F]{7,64}\b', line):
                                    return True
                            if re.search(r'class\s+\w+Adapter', line):
                                return True
                            if 'subsuper-dialog' in line:
                                return True
                            break
            except Exception:
                pass
    return False


def attribute_finding_tool(finding: dict) -> str:
    tool = finding.get('tool')
    if tool and tool != 'unknown':
        return tool

    src = str(finding.get('source', '')).lower()
    if src in ('claude', 'codex', 'agy', 'gemini', 'grok', 'qwen', 'firstmate', 'loop'):
        return src

    p = str(finding.get('path', '')).lower()
    if any(k in p for k in ('antigravity', 'agy')):
        return 'agy'
    if any(k in p for k in ('.gemini', '/gemini/')):
        return 'gemini'
    if any(k in p for k in ('.claude', '/claude/', 'claude-')):
        return 'claude'
    if any(k in p for k in ('.codex', '/codex/', 'codex-')):
        return 'codex'
    if 'qwen' in p:
        return 'qwen'
    if 'grok' in p:
        return 'grok'
    if any(k in p for k in ('firstmate', 'kunchen', 'bin/fm-', 'tests/fm-', 'fm-', '.pi/', '.agents/skills', 'bin/backends/', 'fm_')):
        return 'firstmate'
    if any(k in p for k in ('selfimprove', 'si.py', 'hub_adapters.py', 'tuning.jsonl', 'proposals.jsonl', 'token_records.py', 'watched.json')):
        return 'loop'

    return 'unknown'


def filter_findings(findings: list[dict]) -> list[dict]:
    kept = []
    for f in findings:
        path = f.get('path', '')
        if is_test_fixture_path(path):
            continue
        if is_known_hash_or_checksum_finding(f):
            continue
        f['tool'] = attribute_finding_tool(f)
        kept.append(f)
    return kept


def load_findings(path=None, days=None, now=None):
    if path is None:
        path = findings_path()
    else:
        path = Path(path).expanduser()
    if not path.exists():
        return None
    now = time.time() if now is None else now
    cutoff = (now - days * 86400) if days is not None else 0
    findings = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        ts = parse_finding_ts(item.get('at'))
        if ts >= cutoff:
            findings.append(item)
    return filter_findings(findings)


def count_findings(findings):
    if findings is None:
        return None
    by_severity = {}
    by_rule = {}
    by_tool = {}
    for f in findings:
        sev = f.get('severity', 'unknown')
        by_severity[sev] = by_severity.get(sev, 0) + 1
        rule = f.get('rule', 'unknown')
        by_rule[rule] = by_rule.get(rule, 0) + 1
        tool = attribute_finding_tool(f)
        by_tool[tool] = by_tool.get(tool, 0) + 1
    return {
        'total': len(findings),
        'by_severity': by_severity,
        'by_rule': by_rule,
        'by_tool': by_tool,
    }


def leak_watch_lines(findings):
    lines = ['', '## Leak watch', '']
    if findings is None:
        lines.append('no scanner data yet')
        return lines

    counts = count_findings(findings)
    lines.append(f"- Findings in window: {counts['total']}")

    sev_parts = [f"{k}: {v}" for k, v in sorted(counts['by_severity'].items())]
    lines.append(f"- By severity: {', '.join(sev_parts) if sev_parts else 'none'}")

    rule_parts = [f"{k}: {v}" for k, v in sorted(counts['by_rule'].items())]
    lines.append(f"- By rule: {', '.join(rule_parts) if rule_parts else 'none'}")

    tool_parts = [f"{k}: {v}" for k, v in sorted(counts['by_tool'].items())]
    lines.append(f"- By tool: {', '.join(tool_parts) if tool_parts else 'none'}")

    if findings:
        lines.append('')
        lines.append('### Newest findings')
        newest = sorted(findings, key=lambda f: (parse_finding_ts(f.get('at')), str(f.get('at', ''))), reverse=True)[:10]
        for f in newest:
            rule = f.get('rule', '-')
            tool = attribute_finding_tool(f)
            path = f.get('path', '-')
            line = f.get('line', 0)
            digest = f.get('digest', '-')
            lines.append(f"- {rule}/{tool}/{path}/{line}/{digest}")
    return lines


def goal_scorecard_lines(records, proposals_events, max_lines=120, findings=None):
    proposals, decisions = proposal_state(proposals_events)
    lines = ['', '## Goal scorecard', '', '| Goal | Tools with evidence | Missing | Status |', '|---|---|---|---|']

    finding_counts = {}
    if findings:
        for f in findings:
            t = attribute_finding_tool(f).lower()
            if t:
                finding_counts[t] = finding_counts.get(t, 0) + 1

    for goal in SCORECARD_GOALS:
        tools_with_evidence = []
        tools_missing = []

        for tool in SCORECARD_TOOLS:
            t_records = [r for r in records if r.get('tool', 'qwen').lower() == tool.lower()]
            t_proposals = []
            for idx, p in enumerate(proposals, 1):
                p_goal = normalize_goal(p.get('goal', ''))
                p_tool = p.get('tool', '').lower()
                p_source = p.get('source', '').lower()
                p_idea = p.get('idea', '').lower()
                if p_goal == goal:
                    if (p_tool == tool.lower() or p_source == tool.lower() or
                            tool.lower() in p_idea or (tool == 'loop' and 'loop' in p_idea) or
                            (not p_tool and p_source in ('agent', 'maintainer'))):
                        t_proposals.append((idx, p))

            parts = []
            if t_records:
                done_cnt = sum(r.get('outcome') == 'done' for r in t_records)
                rate = done_cnt / len(t_records)
                parts.append(f'{len(t_records)} tasks ({rate:.0%} done)')
            if t_proposals:
                prop_strs = [f'prop #{idx}' for idx, _ in t_proposals[:2]]
                parts.append(', '.join(prop_strs))

            if goal in ('safe', 'secure'):
                t_cnt = finding_counts.get(tool.lower(), 0)
                if t_cnt > 0:
                    parts.append(f'{t_cnt} findings')

            if parts:
                tools_with_evidence.append(f"{tool} ({'; '.join(parts)})")
            else:
                tools_missing.append(tool)

        evidence_str = '; '.join(tools_with_evidence) if tools_with_evidence else 'none'
        missing_str = ', '.join(tools_missing) if tools_missing else 'none'
        flag = 'ok' if not tools_missing else ('partial' if tools_with_evidence else 'FLAGGED')

        lines.append(f'| {goal} | {evidence_str} | {missing_str} | {flag} |')

    maintainer_lines = ['', '### Maintainer proposals', '',
                        '<!-- Master Claude fills weekly: at least one enhancement/advancement proposal per goal -->']
    for goal in SCORECARD_GOALS:
        maintainer_lines.append(f'- {goal}:')

    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + ['| ... [truncated] | | | |']

    return lines + maintainer_lines


def loop_health_metrics(now=None):
    now = time.time() if now is None else now
    events = [json.loads(line) for line in PROPOSALS.read_text().splitlines() if line.strip()] if PROPOSALS.exists() else []
    proposals, decisions = proposal_state(events)
    tuning_events = read_jsonl(TUNING)

    logged = len(proposals)
    decided = len(decisions)
    adopted = sum(1 for d in decisions.values() if d.get('decision') == 'adopted')
    adoption_rate = (adopted / logged) if logged else 0.0

    decision_days = []
    for idx, d in decisions.items():
        if 1 <= idx <= len(proposals):
            p = proposals[idx - 1]
            p_at, d_at = p.get('at'), d.get('at')
            if p_at is not None and d_at is not None:
                decision_days.append(max(0.0, (d_at - p_at) / 86400.0))
    med_decision_days = median(decision_days)

    tunables_kept = {}
    for e in tuning_events:
        name = e.get('name')
        if e.get('action') == 'keep' and (now - e.get('at', 0)) >= 28 * 86400:
            tunables_kept[name] = True
        elif e.get('action') == 'rollback' and name in tunables_kept:
            tunables_kept[name] = False
    kept_tuning = sum(1 for v in tunables_kept.values() if v)

    kept_decisions = sum(1 for idx, d in decisions.items()
                         if d.get('decision') == 'adopted' and (now - d.get('at', 0)) >= 28 * 86400)

    kept_after_28d = kept_tuning + kept_decisions

    return {
        'proposals_logged': logged,
        'proposals_decided': decided,
        'proposals_adopted': adopted,
        'adoption_rate': adoption_rate,
        'median_days_to_decision': med_decision_days,
        'kept_after_28_days': kept_after_28d,
    }


def loop_health_lines(now=None):
    m = loop_health_metrics(now)
    med_str = f"{m['median_days_to_decision']:.1f}" if m['median_days_to_decision'] is not None else "n/a"
    rate_str = f"{m['adoption_rate']:.1%}"
    return [
        '',
        '## Loop health',
        '',
        f"- Proposals logged: {m['proposals_logged']}",
        f"- Proposals decided: {m['proposals_decided']}",
        f"- Proposals adopted: {m['proposals_adopted']}",
        f"- Proposal adoption rate: {rate_str}",
        f"- Median days to decision: {med_str}",
        f"- Adopted changes kept after 28 days: {m['kept_after_28_days']}",
    ]



def median(values):
    """Median of the numbers in values, ignoring None; None when nothing is left."""
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    sorted_values = sorted(filtered)
    n = len(sorted_values)
    if n % 2 == 1:
        return sorted_values[n // 2]
    else:
        return (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2


def metric_value(records, name):
    """Return a metric, or None for empty/incomplete/invalid measurements.

    Supported names: pass_rate (fraction outcome=done; every outcome must be
    done/failed), minutes_median (median finite nonnegative minutes), and
    quota_used_median (median quota_before minus quota_after; both must be finite
    numbers in [0,100], after <= before). Reject bools as numbers. Do not drop
    incomplete records: all cases must contribute. median and math are available.
    """
    if not records:
        return None
    if name == 'pass_rate':
        if any(r.get('outcome') not in ('done', 'failed') for r in records):
            return None
        return sum(r['outcome'] == 'done' for r in records) / len(records)
    values = []
    for record in records:
        keys = ('minutes',) if name == 'minutes_median' else ('quota_before', 'quota_after')
        if name not in ('minutes_median', 'quota_used_median'):
            return None
        numbers = [record.get(key) for key in keys]
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in numbers):
            return None
        if name == 'quota_used_median':
            before, after = numbers
            if before > 100 or after > before:
                return None
            values.append(before - after)
        else:
            values.append(numbers[0])
    return median(values)


def pass_rate(records, labour):
    """Fraction of records whose 'labour' equals labour and whose 'outcome' is 'done'.

    Returns None when no record has that labour.
    """
    mine = [r for r in records if r.get('labour') == labour]
    return sum(r.get('outcome') == 'done' for r in mine) / len(mine) if mine else None


def top_blocker(records):
    """(text, count) of the blocker text appearing most often across every
    record's 'blockers' list; None when no record has blockers. Ties go to the
    text that sorts first alphabetically."""
    if not records:
        return None

    # Flatten all blocker lists into a single list
    all_blockers = [blocker for record in records for blocker in record.get('blockers', [])]

    # Count occurrences of each blocker
    blocker_counts = {}
    for blocker in all_blockers:
        if blocker in blocker_counts:
            blocker_counts[blocker] += 1
        else:
            blocker_counts[blocker] = 1

    # Find the blocker with the highest count
    if not blocker_counts:
        return None

    max_count = max(blocker_counts.values())
    most_frequent_blockers = [blocker for blocker, count in blocker_counts.items() if count == max_count]

    # Return the first blocker alphabetically
    return (min(most_frequent_blockers), max_count)


def summarize(records):
    labours = sorted({r['labour'] for r in records})
    return {
        'tasks': len(records),
        'done': sum(r['outcome'] == 'done' for r in records),
        'minutes_median': median([r.get('minutes') for r in records]),
        'quota_used_median': median([r['quota_before'] - r['quota_after'] for r in records
                                     if r.get('quota_before') is not None and r.get('quota_after') is not None]),
        'pass_rate': {lab: pass_rate(records, lab) for lab in labours},
        'top_blocker': top_blocker(records),
    }


def propose(summary):
    """At most 3 small proposals, each with expected gain, risk and the proving check."""
    out = []
    for lab, rate in sorted(summary['pass_rate'].items(), key=lambda kv: kv[1]):
        if rate is not None and rate < 0.6:
            out.append({'change': f'route the unit types {lab} failed to a stronger labour, or split them smaller',
                        'gain': f'{lab} pass rate {rate:.0%} -> at least 60%', 'risk': 'more cost on the stronger labour',
                        'check': f'next week {lab} pass rate >= 60% and no rise in quota used per task'})
            break
    if summary['top_blocker'] and summary['top_blocker'][1] >= 2:
        text, n = summary['top_blocker']
        out.append({'change': f'remove the repeated blocker: {text}', 'gain': f'{n} stalls per week -> 0',
                    'risk': 'depends on the fix', 'check': f'blocker "{text}" absent from next week\'s records'})
    return out[:3]


def load(days):
    if not RECORDS.exists():
        return []
    cutoff = time.time() - days * 86400
    return [r for r in map(json.loads, RECORDS.read_text().splitlines()) if r.get('at', 0) >= cutoff]


def nonblank(value):
    """Argparse type: strip a string and reject empty/whitespace with ArgumentTypeError."""
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError('must contain non-whitespace text')
    return value


def proposal_state(events):
    """Return proposal rows and latest decision by stable, one-based proposal index."""
    proposals, decisions = [], {}
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('proposal log entries must be JSON objects')
        if event.get('type') == 'decision':
            index = event.get('proposal')
            if type(index) is not int or not 1 <= index <= len(proposals):
                raise ValueError('decision references an unknown proposal')
            if event.get('decision') not in ('adopted', 'rejected', 'later'):
                raise ValueError('unknown decision')
            decisions[index] = event
        else:
            proposals.append(event)
    return proposals, decisions


def append_proposal_event(build, path=None):
    # Lock the whole read/validate/append so concurrent agents get unique indexes.
    p = Path(path) if path else PROPOSALS
    with p.open('a+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        proposals, decisions = proposal_state(events)
        event, index = build(proposals, decisions)
        f.seek(0, os.SEEK_END)
        f.write(('\n' if raw and not raw.endswith('\n') else '') + json.dumps(event) + '\n')
        return index


def load_similarity_threshold(tunables_path=None, default=0.65) -> float:
    p = Path(tunables_path) if tunables_path else TUNABLES
    if not p.exists():
        return default
    try:
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        if isinstance(data, dict):
            if 'vector_similarity_threshold' in data:
                return float(data['vector_similarity_threshold'])
            if 'similarity_threshold' in data:
                return float(data['similarity_threshold'])
            for entry in data.get('tunables', []):
                if isinstance(entry, dict) and 'vector' in entry.get('name', '').lower() and 'threshold' in entry.get('name', '').lower():
                    return float(entry.get('current', default))
    except Exception:
        pass
    return default


def log_hint(query: str, top_hit: str, score: float, source: str = 'query', hints_path: Path | str = None) -> None:
    """Append a vector search hint to hints.jsonl for the R18 trial measure."""
    p = Path(hints_path) if hints_path else HINTS
    entry = {
        'at': int(time.time()),
        'query': query,
        'top_hit': top_hit,
        'score': round(float(score), 4),
        'source': source,
    }
    try:
        with p.open('a', encoding='utf-8') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(entry) + '\n')
    except Exception:
        pass


def load_hints(hints_path=None, days=None):
    path = Path(hints_path) if hints_path else HINTS
    if not path.exists():
        return []
    cutoff = (time.time() - days * 86400) if days else None
    hints = []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip():
            continue
        try:
            h = json.loads(line)
            if cutoff and h.get('at', 0) < cutoff:
                continue
            hints.append(h)
        except Exception:
            continue
    return hints


def check_related_proposals(idea: str, proposals_path=None, db_path=None, tunables_path=None, hints_path=None, embedder=None):
    p_path = Path(proposals_path) if proposals_path else PROPOSALS
    existing_props = []
    if p_path.exists():
        raw = p_path.read_text(encoding='utf-8', errors='ignore')
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        existing_props, _ = proposal_state(events)

    if not existing_props:
        return

    # 1. Exact/normalized match check
    norm_idea = graph.normalize_name(idea)
    exact_matches = []
    for idx, prop in enumerate(existing_props, 1):
        p_idea = prop.get('idea', '')
        if graph.normalize_name(p_idea) == norm_idea:
            exact_matches.append((idx, p_idea))

    for idx, p_idea in exact_matches:
        print(f'exact match: proposal #{idx}: "{p_idea}"')

    # 2. Vector search check
    threshold = load_similarity_threshold(tunables_path)
    cand_map = {}
    for idx, prop in enumerate(existing_props, 1):
        pi = prop.get('idea')
        if pi and pi not in cand_map:
            cand_map[pi] = idx
    candidate_ideas = list(cand_map.keys())

    related = []
    if candidate_ideas:
        try:
            p_db = Path(db_path) if db_path else GRAPH_DB
            if embedder is not None:
                related = graph.find_related_items(
                    idea, candidate_ideas, embedder, db_path=p_db, threshold=threshold, top_k=5, exact_match_filter=idea
                )
            else:
                with graph.LlamaServerEmbedder() as emb:
                    related = graph.find_related_items(
                        idea, candidate_ideas, emb, db_path=p_db, threshold=threshold, top_k=5, exact_match_filter=idea
                    )
        except Exception as e:
            print(f"vector search offline: {e}; falling back to exact match")
            related = []

    for r_idea, score in related:
        p_idx = cand_map.get(r_idea)
        idx_str = f" #{p_idx}" if p_idx else ""
        print(f'possibly related: proposal{idx_str} (score: {score:.2f}): "{r_idea}"')

    if related:
        top_idea, top_score = related[0]
        top_idx = cand_map.get(top_idea)
        log_hint(
            idea,
            f"proposal:{top_idx}: {top_idea}" if top_idx else top_idea,
            top_score,
            source='propose',
            hints_path=hints_path,
        )


def cmd_propose(a):
    check_related_proposals(
        a.idea,
        proposals_path=getattr(a, 'proposals', None),
        db_path=getattr(a, 'db', None),
        tunables_path=getattr(a, 'tunables', None),
        hints_path=getattr(a, 'hints', None),
        embedder=getattr(a, '_embedder', None),
    )
    goal = normalize_goal(getattr(a, 'goal', None))
    tool = getattr(a, 'tool', None)

    def build(proposals, decisions):
        entry = {'at': int(time.time()), 'source': a.source, 'idea': a.idea, 'why': a.why,
                 'expected_gain': a.gain, 'risk': a.risk, 'check': a.check,
                 'status': 'open'}
        if goal:
            entry['goal'] = goal
        if tool:
            entry['tool'] = tool
        return entry, len(proposals) + 1
    index = append_proposal_event(build, path=getattr(a, 'proposals', None))
    print(f'proposal: {index}')
    print('help: si.py review')


def cmd_decide(a):
    trial_measure = getattr(a, 'trial_measure', None)
    trial_date = getattr(a, 'trial_date', None)
    verdict = getattr(a, 'verdict', None)

    def build(proposals, decisions):
        if not 1 <= a.index <= len(proposals):
            raise ValueError(f'unknown proposal index {a.index}; run si.py review')
        event = {'at': int(time.time()), 'type': 'decision', 'proposal': a.index,
                 'decision': a.decision, 'note': a.note}
        prior_dec = decisions.get(a.index) or {}
        measure = trial_measure or (prior_dec.get('trial_measure') if a.decision == 'adopted' else None)
        if measure:
            event['trial_measure'] = measure
            if trial_date:
                event['trial_date'] = trial_date
            elif prior_dec.get('trial_date') and not trial_measure:
                event['trial_date'] = prior_dec['trial_date']
            else:
                dt = datetime.fromtimestamp(event['at'], tz=timezone.utc)
                event['trial_date'] = (dt + timedelta(days=7)).strftime('%Y-%m-%d')
        if verdict:
            event['verdict'] = verdict
        return event, a.index
    index = append_proposal_event(build)
    print(f'proposal: {index}')
    print(f'decision: {a.decision}')


def trial_verdict(decision, now=None):
    """Determine trial verdict: 'keep', 'clean up', or 'pending' until the review date passes."""
    explicit = decision.get('verdict')
    if explicit in ('keep', 'clean up'):
        return explicit
    review_date = decision.get('trial_date')
    if not review_date:
        dt = datetime.fromtimestamp(decision.get('at', 0), tz=timezone.utc)
        review_date = (dt + timedelta(days=7)).strftime('%Y-%m-%d')
    if now is None:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    elif isinstance(now, (int, float)):
        today = datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y-%m-%d')
    elif isinstance(now, datetime):
        today = now.strftime('%Y-%m-%d')
    else:
        today = str(now)
    if today < review_date:
        return 'pending'
    return explicit or 'clean up'


def trials_lines(proposals_events=None, now=None):
    """Review section for R18: list adopted proposals with trial measures."""
    if proposals_events is None:
        proposals_events = read_jsonl(PROPOSALS) if PROPOSALS.exists() else []
    proposals, decisions = proposal_state(proposals_events)
    trials = []
    for index, prop in enumerate(proposals, 1):
        dec = decisions.get(index)
        if not dec or dec.get('decision') != 'adopted':
            continue
        measure = dec.get('trial_measure') or dec.get('trial') or dec.get('measure')
        if not measure:
            continue
        review_date = dec.get('trial_date')
        if not review_date:
            dt = datetime.fromtimestamp(dec.get('at', 0), tz=timezone.utc)
            review_date = (dt + timedelta(days=7)).strftime('%Y-%m-%d')
        verdict = trial_verdict(dec, now=now)
        trials.append({
            'index': index,
            'idea': prop.get('idea', ''),
            'measure': measure,
            'review_date': review_date,
            'verdict': verdict,
        })
    lines = ['', '## Trials', '']
    if not trials:
        lines.append('No active trials.')
        return lines
    lines += ['| # | Proposal | Measure | Review date | Verdict |',
              '|---|---|---|---|---|']
    for t in trials:
        lines.append(f"| {t['index']} | {t['idea']} | {t['measure']} | {t['review_date']} | {t['verdict']} |")
    return lines


def proposal_log_lines():
    events = [json.loads(line) for line in PROPOSALS.read_text().splitlines() if line.strip()] if PROPOSALS.exists() else []
    proposals, decisions = proposal_state(events)
    groups = {}
    for index, proposal in enumerate(proposals, 1):
        if proposal.get('status') in ('open', 'building', 'info'):
            groups.setdefault(proposal.get('source', 'unknown'), []).append((index, proposal))
    lines = ['', "## Proposal log (for the captain's decision)", '']
    for source, entries in sorted(groups.items()):
        lines += [f'### {source}', '']
        for index, proposal in sorted(entries, key=lambda entry: (entry[1].get('at', 0), entry[0])):
            lines += [f'{index}. {proposal["idea"]} [{proposal["status"]}]',
                      f'   Why: {proposal.get("why", "not specified")}',
                      f'   Expected gain: {proposal.get("expected_gain", "not specified")}',
                      f'   Risk: {proposal.get("risk", "not specified")}',
                      f'   Proving check: {proposal.get("check", "not specified")}']
            if proposal.get('goal'):
                lines += [f'   Goal: {proposal["goal"]}']
            decision = decisions.get(index)
            if decision:
                lines += [f'   Latest decision: {decision["decision"]}' +
                          (f' — {decision["note"]}' if decision.get('note') else '')]
            else:
                lines += ['   Latest decision: pending']
        lines += ['']
    return lines if groups else lines + ['No open, building or info proposals.']


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def local_path(name):
    """Only existing regular files inside this repo; never follow symlinks."""
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError('tuning paths must be relative to this repository')
    path = ROOT / name
    if '..' in Path(name).parts or any(part.startswith('.') for part in Path(name).parts):
        raise ValueError('hidden paths and parent traversal are forbidden')
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('tuning paths must not contain symlinks')
    if not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError(f'tuning requires an existing local file: {name}')
    return path


def content_fingerprint(parts):
    """SHA-256 hex digest of ordered (label: str, content: bytes) pairs.

    Use hashlib.sha256(). For each pair, encode label as UTF-8, then process
    label bytes followed by content bytes. Before each byte string, hash its
    length as an unsigned 8-byte big-endian integer, then hash the string.
    Return hexdigest(), including for empty parts. Do not sort the pairs.
    """
    digest = hashlib.sha256()
    for label, content in parts:
        for chunk in (label.encode('utf-8'), content):
            digest.update(len(chunk).to_bytes(8, 'big'))
            digest.update(chunk)
    return digest.hexdigest()


def frozen_set(name):
    """Hash manifest bytes plus every declared input, including tests/protocol."""
    path = local_path(name)
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError('test-set manifest must be a JSON object')
    cases, files = manifest.get('cases'), manifest.get('files')
    if (not isinstance(cases, list) or not cases or
            any(not isinstance(case, str) or not case.strip() for case in cases) or
            len(set(cases)) != len(cases) or not isinstance(files, list) or not files or
            any(not isinstance(file, str) for file in files) or len(set(files)) != len(files)):
        raise ValueError('test-set manifest needs unique nonempty cases and input files')
    parts = [(file, local_path(file).read_bytes()) for file in sorted(files)]
    if 'sha256' in manifest and manifest['sha256'] != content_fingerprint(parts):
        raise ValueError('frozen test-set input sha256 fingerprint mismatch')
    return content_fingerprint([(name, raw), *parts]), cases, {path, *(local_path(file) for file in files)}


def catalog():
    data = json.loads(TUNABLES.read_text())
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('tunables'), list):
        raise ValueError('tunables.json requires version 1 and a tunables list')
    names = [entry.get('name') for entry in data['tunables'] if isinstance(entry, dict)]
    if len(names) != len(data['tunables']) or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError('tunables must have unique nonempty names')
    return data


def in_bounds(entry, value):
    if type(value) is not int:
        return False
    if 'allowed' in entry:
        allowed = entry['allowed']
        return ('min' not in entry and 'max' not in entry and isinstance(allowed, list) and
                bool(allowed) and all(type(v) is int for v in allowed) and value in allowed)
    return (type(entry.get('min')) is int and type(entry.get('max')) is int and
            entry['min'] <= value <= entry['max'])


def policy(entry):
    return {key: value for key, value in entry.items() if key != 'current'}


def setting(entry):
    if entry.get('adapter') != 'local_json':
        raise ValueError('candidate only; a reviewed local_json adapter is required')
    path = local_path(entry.get('file'))
    if path.suffix != '.json' or path in (TUNABLES, TUNING, RECORDS, PROPOSALS):
        raise ValueError('setting must be a separate local JSON file')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(entry.get('key'), str) or entry['key'] not in data:
        raise ValueError('setting key must already exist in a JSON object')
    return path, data


def atomic_json(path, data):
    """Atomic replacement in the same directory, retaining the file's mode."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply_value(data, trial, value):
    """Compare before writing; leave unrelated keys and manual edits intact."""
    entry = trial['entry']
    if catalog() != data:
        raise ValueError('catalog changed concurrently; rerun si.py tune')
    path, content = setting(entry)
    actual = content[entry['key']]
    if type(actual) is not int or actual not in (trial['old'], trial['new']):
        raise ValueError('setting changed outside this trial; manual reconciliation required')
    if actual != value:
        content[entry['key']] = value
        atomic_json(path, content)
    if catalog() != data:
        raise ValueError('catalog changed during application; rerun si.py tune to recover')
    for current in data['tunables']:
        if current['name'] == entry['name'] and all(current.get(k) == entry.get(k) for k in ('file', 'key', 'adapter')):
            current['current'] = value
    atomic_json(TUNABLES, data)


def active_trial(events):
    active = None
    for event in events:
        if event['action'] == 'trial_start':
            if active is not None:
                raise ValueError('tuning log has overlapping trials')
            active = event['trial']
        elif event['action'] in ('keep', 'rollback'):
            if active is None or event.get('trial_id') != active['id']:
                raise ValueError('tuning log has an unmatched decision')
            active = None
    return active


def measured(records, trial, value, trial_id, now):
    """One first record per frozen case; repeats cannot increase sample size."""
    selected = {}
    start, end = (trial['at'], now) if trial_id else (trial['at'] - 7 * 86400, trial['at'])
    for row in records:
        tag = row.get('tuning', {})
        case = tag.get('case')
        if (tag.get('name') == trial['entry']['name'] and tag.get('hash') == trial['hash'] and
                type(tag.get('value')) is int and tag['value'] == value and tag.get('trial') == trial_id and
                case in trial['cases'] and start <= row.get('at', 0) < end):
            selected.setdefault(case, row)
    rows = list(selected.values())
    metrics = {role: metric_value(rows, trial['entry'][role]['name']) for role in ('target', 'guard')}
    enough = len(rows) >= trial['entry']['min_samples'] and set(selected) == set(trial['cases'])
    return dict(samples=len(rows), complete=enough and all(v is not None for v in metrics.values()), **metrics)


def measure_reverse(records, trial, now):
    """Re-measure with previous value (trial['old']) on the same frozen set.

    Checks for records measuring trial['old'] after trial start; falls back
    to baseline if no separate reverse records were recorded.
    """
    selected = {}
    for row in records:
        tag = row.get('tuning', {})
        case = tag.get('case')
        if (tag.get('name') == trial['entry']['name'] and tag.get('hash') == trial['hash'] and
                type(tag.get('value')) is int and tag['value'] == trial['old'] and
                case in trial['cases'] and (row.get('at', 0) >= trial['at'] or tag.get('reverse') is True or tag.get('trial') == trial['id'])):
            selected.setdefault(case, row)

    rows = list(selected.values())
    metrics = {role: metric_value(rows, trial['entry'][role]['name']) for role in ('target', 'guard')}
    enough = len(rows) >= trial['entry']['min_samples'] and set(selected) == set(trial['cases'])
    if enough and all(v is not None for v in metrics.values()):
        return dict(samples=len(rows), complete=True, **metrics)
    return trial['baseline']


def tune(now=None):
    """Serialized weekly controller; log intent durably before changing a setting."""
    now = time.time() if now is None else now
    week = datetime.fromtimestamp(now, timezone.utc).strftime('%G-W%V')
    actions = []
    with TUNING.open('a+') as journal:
        fcntl.flock(journal, fcntl.LOCK_EX)
        journal.seek(0)
        raw = journal.read()
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        def emit(action, name, reason, **extra):
            nonlocal raw
            event = dict(at=now, week=week, action=action, name=name, reason=reason, **extra)
            journal.seek(0, os.SEEK_END)
            journal.write(('\n' if raw and not raw.endswith('\n') else '') + json.dumps(event, allow_nan=False) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
            raw = '\n'
            events.append(event)
            actions.append(event)
        data = catalog()
        trial = active_trial(events)
        if trial:
            entry = trial['entry']
            current = next((e for e in data['tunables'] if e['name'] == entry['name']), None)
            pending = next((e for e in reversed(events) if e['action'] == 'rollback_pending' and e.get('trial_id') == trial['id']), None)
            reason = pending['reason'] if pending else None
            if not reason:
                try:
                    digest, _, _ = frozen_set(entry['test_set'])
                    if digest != trial['hash']:
                        reason = 'void: frozen test-set hash changed'
                    elif current is None or current.get('approved') is not True or policy(current) != policy(entry):
                        reason = 'void: approval or policy changed'
                    elif setting(entry)[1][entry['key']] != trial['new'] or current['current'] != trial['new']:
                        reason = 'void: trial application interrupted or setting changed'
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    reason = f'void: frozen inputs or configuration unavailable: {exc}'
            if not reason and now - trial['at'] < 7 * 86400:
                emit('waiting', entry['name'], 'measure after at least seven days', trial_id=trial['id'])
                return actions
            result, reverse = None, None
            improved, safe = False, False
            if not reason:
                result = measured(read_jsonl(RECORDS), trial, trial['new'], trial['id'], now)
                if not result['complete']:
                    emit('extend', entry['name'], 'skipped for small sample or incomplete metrics', trial_id=trial['id'], result=result)
                    return actions
                baseline = trial['baseline']
                improved = result['target'] > baseline['target'] if entry['target']['direction'] == 'higher' else result['target'] < baseline['target']
                safe = result['guard'] >= baseline['guard'] if entry['guard']['direction'] == 'higher' else result['guard'] <= baseline['guard']
                if improved and safe:
                    reverse = measure_reverse(read_jsonl(RECORDS), trial, now)
                    gain_disappeared = (reverse['target'] < result['target'] if entry['target']['direction'] == 'higher'
                                        else reverse['target'] > result['target'])
                    if gain_disappeared:
                        emit('keep', entry['name'], 'target improved and guard did not worsen', trial_id=trial['id'],
                             result=result, reverse=reverse)
                        return actions
                    reason = 'unattributed'
                else:
                    reason = 'guard regressed' if not safe else 'target did not improve'
            extra = dict(result=result)
            if reverse is not None:
                extra['reverse'] = reverse
            if not pending:
                emit('rollback_pending', entry['name'], reason, trial_id=trial['id'], **extra)
            apply_value(data, trial, trial['old'])
            emit('rollback', entry['name'], reason, trial_id=trial['id'], **extra)
            return actions
        if any(e['week'] == week and e['action'] in ('trial_start', 'keep', 'rollback') for e in events):
            emit('skipped', '-', 'weekly change limit or decision cooldown')
            return actions
        records = read_jsonl(RECORDS)
        for entry in data['tunables']:
            name = entry['name']
            if entry.get('approved') is not True:
                emit('skipped', name, 'not captain approved')
                continue
            try:
                if (type(entry.get('step')) is not int or entry['step'] == 0 or
                        not in_bounds(entry, entry.get('current')) or
                        type(entry.get('min_samples')) is not int or entry['min_samples'] < 2):
                    raise ValueError('invalid approved bounds, current value, step, or minimum sample size')
                for role in ('target', 'guard'):
                    metric = entry[role]
                    if metric['name'] not in ('pass_rate', 'minutes_median', 'quota_used_median') or metric['direction'] not in ('higher', 'lower'):
                        raise ValueError('unsupported metric or direction')
                new = entry['current'] + entry['step']
                if not in_bounds(entry, new):
                    raise ValueError('approved step would exceed approved bounds')
                path, content = setting(entry)
                if type(content[entry['key']]) is not int or content[entry['key']] != entry['current']:
                    raise ValueError('declared current value differs from setting')
                digest, cases, inputs = frozen_set(entry['test_set'])
                if path in inputs or TUNABLES in inputs:
                    raise ValueError('setting/catalog must be separate from frozen inputs')
                if any(e['action'] == 'trial_start' and policy(e['trial']['entry']) == policy(entry) and
                       e['trial']['old'] == entry['current'] and e['trial']['new'] == new for e in events):
                    raise ValueError('step already tried under this policy; renewed captain policy required')
                trial = dict(id=uuid.uuid4().hex, at=now, entry=dict(entry), old=entry['current'], new=new,
                             hash=digest, cases=cases)
                baseline = measured(records, trial, trial['old'], None, now)
                if not baseline['complete']:
                    emit('extend', name, 'skipped for small baseline sample or incomplete metrics', result=baseline)
                    continue
                trial['baseline'] = baseline
            except (OSError, ValueError, KeyError, TypeError) as exc:
                emit('skipped', name, str(exc))
                continue
            emit('trial_start', name, 'target and guard frozen before approved step', trial=trial, trial_id=trial['id'])
            apply_value(data, trial, new)
            break
    return actions


def cmd_tune(a):
    actions = tune()
    print(f'actions[{len(actions)}]:')
    for event in actions:
        # JSON quoting is also TOON quoting for these scalar strings.
        print('  - ' + json.dumps(f"{event['name']}: {event['action']}: {event['reason']}"))
    print('help: si.py review')


def tuning_lines(days):
    entries = catalog()['tunables'] if TUNABLES.exists() else []
    lines = ['', '## Tier 1 tuning', '', '### Current approved bounds', '']
    approved = [e for e in entries if e.get('approved') is True]
    for entry in approved:
        bounds = entry.get('allowed', [entry.get('min'), entry.get('max')])
        lines.append(f"- {entry['name']}: current={entry.get('current')}; bounds={bounds}; step={entry.get('step')}; target={entry.get('target')}; guard={entry.get('guard')}; minimum samples={entry.get('min_samples')}; approved by={entry.get('approved_by', 'unspecified')} on={entry.get('approved_at', 'unspecified')}")
    if not approved:
        lines.append('No approved tunables; captain approval is required before any change.')
    events = read_jsonl(TUNING)
    lines += ['', '### Actions in this reporting window', '']
    for event in events:
        if event['at'] >= time.time() - days * 86400:
            details = event.get('result')
            if event['action'] == 'trial_start':
                trial = event['trial']
                details = dict(old=trial['old'], new=trial['new'], baseline=trial['baseline'],
                               target=trial['entry']['target'], guard=trial['entry']['guard'], hash=trial['hash'])
            lines.append(f"- {event['name']}: {event['action']} — {event['reason']} (trial={event.get('trial_id', '-')})" +
                         (f'; evidence={json.dumps(details)}' if details else ''))
    if lines[-1] == '':
        lines.append('No tuning actions in this window.')
    trial = active_trial(events)
    if trial:
        lines += ['', f"Active trial: {trial['id']} ({trial['entry']['name']}, {trial['old']} -> {trial['new']}); frozen hash {trial['hash']}."]
    return lines


def cmd_record(a):
    tool = getattr(a, 'tool', None) or 'qwen'
    rec = {'at': int(time.time()), 'task': a.task, 'master': a.master, 'labour': a.labour,
           'tool': tool, 'quota_before': a.quota_before, 'quota_after': a.quota_after, 'minutes': a.minutes,
           'checks': a.checks, 'blockers': a.blocker or [], 'outcome': a.outcome}
    learned = getattr(a, 'learned', None)
    if learned and isinstance(learned, str) and learned.strip():
        rec['learned'] = learned.strip()
    if bool(a.benchmark) != bool(a.case):
        raise ValueError('--benchmark and --case must be provided together')
    if a.benchmark:
        # Share the controller lock so a record cannot straddle a setting change.
        with TUNING.open('a+') as journal:
            fcntl.flock(journal, fcntl.LOCK_EX)
            entries = catalog()['tunables']
            entry = next((e for e in entries if e['name'] == a.benchmark), None)
            if entry is None:
                raise ValueError('unknown benchmark tunable; inspect tunables.json')
            digest, cases, _ = frozen_set(entry.get('test_set'))
            if a.case not in cases:
                raise ValueError('case is not in the frozen manifest')
            value = setting(entry)[1][entry['key']]
            if type(value) is not int or value != entry.get('current'):
                raise ValueError('setting and declared current value must agree')
            trial = active_trial(read_jsonl(TUNING))
            if trial and trial['entry']['name'] != entry['name']:
                raise ValueError('another tunable is under trial; baseline measurement refused')
            if getattr(a, 'reverse', False):
                if not trial:
                    raise ValueError('--reverse requires an active trial')
                if trial['hash'] != digest:
                    raise ValueError('trial inputs changed')
                rec['tuning'] = dict(name=entry['name'], value=trial['old'], hash=digest, case=a.case,
                                     trial=trial['id'], reverse=True)
            else:
                if trial and (trial['hash'] != digest or trial['new'] != value):
                    raise ValueError('trial inputs or value changed; run si.py tune to roll back')
                if trial and (entry.get('approved') is not True or policy(entry) != policy(trial['entry'])):
                    raise ValueError('trial approval changed; run si.py tune to roll back')
                rec['tuning'] = dict(name=entry['name'], value=value, hash=digest, case=a.case,
                                     trial=trial['id'] if trial else None)
            rec['at'] = time.time()
            with RECORDS.open('a') as stream:
                stream.write(json.dumps(rec) + '\n')
    else:
        with RECORDS.open('a') as f:
            f.write(json.dumps(rec) + '\n')
    print('recorded', a.task)


def normalize_learned_text(text):
    if not isinstance(text, str):
        return ''
    return ' '.join(text.strip().lower().split())


def learned_limits_lines(records, all_records=None, days=7, now=None):
    """List the week's learned lines and mark any that match an earlier week's."""
    now_ts = time.time() if now is None else (now if isinstance(now, (int, float)) else now.timestamp())
    cutoff = now_ts - days * 86400

    earlier_learned = set()
    if all_records:
        for r in all_records:
            if not isinstance(r, dict):
                continue
            if r.get('at', 0) < cutoff:
                val = r.get('learned')
                if val and isinstance(val, str) and val.strip():
                    earlier_learned.add(normalize_learned_text(val))

    week_learned = []
    for r in records:
        if not isinstance(r, dict):
            continue
        val = r.get('learned')
        if val and isinstance(val, str) and val.strip():
            week_learned.append(val.strip())

    lines = ['', '## Learned limits', '']
    if not week_learned:
        lines.append('No learned limits recorded this week.')
        return lines

    for line in week_learned:
        norm = normalize_learned_text(line)
        if norm in earlier_learned:
            lines.append(f'- {line} [recurred]')
        else:
            lines.append(f'- {line}')
    return lines


def in_bucket(val, low, high):
    """Check if numeric val falls in bucket [low, high). None represents infinity."""
    if val is None or not isinstance(val, (int, float)):
        return False
    if low is not None and val < low:
        return False
    if high is not None and val >= high:
        return False
    return True


def compute_pass_rate_stats(items):
    """Return (count, passed_count, pass_rate_str)."""
    n = len(items)
    if n == 0:
        return 0, 0, 'N/A'
    passed = sum(1 for item in items if item.get('verdict') == 'pass')
    pct = (passed / n) * 100.0
    return n, passed, f'{pct:.1f}%'


def load_driver_jobs(results_dir=DRIVER_RESULTS_DEFAULT):
    """Load Qwen driver jobs from results/<run>/verdict.json.

    Skips older files without a 'jobs' list or malformed files without error.
    Returns list of dicts with keys: prompt_tokens, output_lines, verdict, typecheck_enabled, etc.
    """
    results_path = Path(results_dir) if results_dir else DRIVER_RESULTS_DEFAULT
    if not results_path.exists():
        return []

    verdict_files = sorted(results_path.glob('*/verdict.json'))
    if not verdict_files:
        verdict_files = sorted(results_path.glob('verdict.json'))

    jobs = []
    for vf in verdict_files:
        try:
            data = json.loads(vf.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        run_jobs = data.get('jobs')
        if not isinstance(run_jobs, list):
            continue
        typecheck_enabled = bool(data.get('typecheck_enabled', False))
        for j in run_jobs:
            if not isinstance(j, dict):
                continue
            tc = j.get('typecheck_enabled')
            if tc is None:
                tc = typecheck_enabled
            else:
                tc = bool(tc)
            jobs.append({
                'prompt_tokens': j.get('prompt_tokens'),
                'output_lines': j.get('output_lines'),
                'verdict': j.get('verdict'),
                'typecheck_enabled': tc,
                'typecheck_exit_code': j.get('typecheck_exit_code'),
                'model_seconds': j.get('model_seconds'),
            })
    return jobs


def qwen_job_size_lines(results_dir=DRIVER_RESULTS_DEFAULT, jobs=None):
    """Review section 'Qwen job size': pass rate by prompt-token and output-line buckets, plus typecheck."""
    if jobs is None:
        jobs = load_driver_jobs(results_dir)
    lines = ['', '## Qwen job size', '']
    if not jobs:
        lines.append('No Qwen driver jobs recorded.')
        return lines

    lines += ['### Prompt tokens', '',
              '| Bucket | Jobs | Passed | Pass rate |',
              '|---|---|---|---|']
    for low, high, label in PROMPT_TOKEN_BUCKETS:
        sub = [j for j in jobs if in_bucket(j.get('prompt_tokens'), low, high)]
        n, passed, rate = compute_pass_rate_stats(sub)
        lines.append(f'| {label} | {n} | {passed} | {rate} |')

    lines += ['', '### Output lines', '',
              '| Bucket | Jobs | Passed | Pass rate |',
              '|---|---|---|---|']
    for low, high, label in OUTPUT_LINE_BUCKETS:
        sub = [j for j in jobs if in_bucket(j.get('output_lines'), low, high)]
        n, passed, rate = compute_pass_rate_stats(sub)
        lines.append(f'| {label} | {n} | {passed} | {rate} |')

    lines += ['', '### Typecheck', '',
              '| Typecheck | Jobs | Passed | Pass rate |',
              '|---|---|---|---|']
    for label, val in [('on', True), ('off', False)]:
        sub = [j for j in jobs if j.get('typecheck_enabled') is val]
        n, passed, rate = compute_pass_rate_stats(sub)
        lines.append(f'| {label} | {n} | {passed} | {rate} |')

    return lines


def default_fetcher(tool):
    """Only reads; commands or read-only GET requests."""
    if 'command' in tool:
        res = subprocess.run(tool['command'], shell=True, capture_output=True, text=True, timeout=10)
        return {'type': 'version', 'output': res.stdout.strip() or res.stderr.strip()}
    elif 'changelog_url' in tool:
        import urllib.request
        req = urllib.request.Request(tool['changelog_url'], headers={'User-Agent': 'selfimprove/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode('utf-8', errors='replace')
        return {'type': 'changelog', 'output': text}
    return None


def extract_headings(text):
    headings = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#'):
            h = line.lstrip('#').strip()
            if h:
                headings.append(h)
        elif any(line.lower().startswith(p) for p in ('release ', 'version ', 'v')):
            headings.append(line)
    return headings


def scan(fetch=False, fetcher=None, watched_path=None, advancements_path=None, now=None):
    watched_path = Path(watched_path) if watched_path else WATCHED
    advancements_path = Path(advancements_path) if advancements_path else ADVANCEMENTS
    now = time.time() if now is None else now

    if not watched_path.exists():
        return {'tools': [], 'new_items': [], 'advancements': []}

    watched_tools = json.loads(watched_path.read_text())
    if not isinstance(watched_tools, list):
        raise ValueError('watched.json must contain a list of tool specifications')

    if not fetch:
        return {'tools': watched_tools, 'new_items': [], 'advancements': []}

    fetcher = fetcher or default_fetcher
    prev_advancements = read_jsonl(advancements_path)
    prev_items = {a.get('item') for a in prev_advancements if a.get('item')}

    new_items = []
    for tool in watched_tools:
        name = tool.get('name')
        if not name:
            continue
        try:
            res = fetcher(tool)
        except Exception:
            continue
        if not res or not res.get('output'):
            continue

        detected = []
        if res['type'] == 'version':
            ver_line = res['output'].splitlines()[0].strip()
            if ver_line and ver_line not in prev_items:
                detected.append(('version', ver_line))
        elif res['type'] == 'changelog':
            headings = extract_headings(res['output'])
            for h in headings[:10]:
                if h not in prev_items:
                    detected.append(('heading', h))

        for item_type, item in detected:
            def build_prop(proposals, decisions):
                event = {'at': int(now), 'source': f'scan:{name}',
                         'idea': f'Evaluate upstream advancement: {item}',
                         'why': f'New {item_type} detected for {name}',
                         'expected_gain': 'Upstream capability or performance improvement',
                         'risk': 'Needs review before adoption',
                         'check': 'Run benchmark on frozen cases',
                         'status': 'open', 'goal': 'advanced', 'tool': name}
                return event, len(proposals) + 1

            prop_idx = append_proposal_event(build_prop)
            prev_items.add(item)
            adv_entry = {'at': int(now), 'tool': name, 'type': item_type, 'item': item, 'proposal': prop_idx}
            with advancements_path.open('a') as f:
                f.write(json.dumps(adv_entry) + '\n')
            new_items.append(adv_entry)

    return {'tools': watched_tools, 'new_items': new_items, 'advancements': read_jsonl(advancements_path)}


import hub_adapters as ha

HF_PUBLISHERS = [
    'Qwen', 'bartowski', 'unsloth', 'lmstudio-community',
    'deepseek-ai', 'microsoft', 'google', 'mistralai'
]
BENCH2_DOWNLOADS_DEFAULT = Path.home() / 'local-llm' / 'models' / 'bench2-downloads.json'
MODELS_DIR_DEFAULT = Path.home() / 'local-llm' / 'models'
BENCH2_ABTEST_SCRIPT = Path.home() / 'local-llm' / 'bench2' / 'run-abtest.sh'
BENCH2_HEAD_TO_HEAD_SCRIPT = Path.home() / 'local-llm' / 'bench2' / 'head-to-head.py'
BENCH2_CANDIDATE_CONFIG_DEFAULT = Path.home() / 'local-llm' / 'bench2' / 'candidate.json'
TESTED_MODELS_DEFAULT = Path.home() / 'local-llm' / 'bench-logs' / 'local-model-ab-test' / 'tested_models.json'
BENCH_LOGS_DEFAULT = Path.home() / 'local-llm' / 'bench-logs' / 'local-model-ab-test'
HUB_FIXTURES_DEFAULT = Path(__file__).parent / 'tests' / 'fixtures' / 'hub_fixtures.json'


def parse_param_count(model_id: str):
    """Extract parameter count as float from a model identifier or name (e.g. 7B -> 7e9), or None if unknown."""
    m = re.search(r'(?:^|[^\w])(\d+(?:\.\d+)?)\s*[bB](?:[^\w]|$)', model_id)
    if m:
        return float(m.group(1)) * 1e9
    return None


def estimate_q4_km_size_bytes(params: float) -> float:
    """Calculate estimated Q4_K_M size in bytes: params * 4.85 bits / 8."""
    return params * 4.85 / 8


def model_fits_threshold(params: float, max_bytes: float = 6 * (1024**3)) -> bool:
    """Return True if the estimated Q4_K_M size is <= max_bytes (default 6 GB)."""
    return estimate_q4_km_size_bytes(params) <= max_bytes


def parse_hf_models(items: list, max_age_days: int = 7, now: float = None) -> list:
    """Parse raw HF models listing and filter to recent coder/instruct GGUF models."""
    now = time.time() if now is None else now
    parsed = []
    for item in items:
        repo_id = item.get('id') or item.get('modelId') or ''
        if not repo_id:
            continue
        tags = [str(t) for t in item.get('tags', [])]

        # GGUF check: must be in repo name or tags
        is_gguf = 'gguf' in repo_id.lower() or any('gguf' in t.lower() for t in tags)
        if not is_gguf:
            continue

        # Coder/instruct check
        all_text = f"{repo_id} {' '.join(tags)}".lower()
        if not any(kw in all_text for kw in ('coder', 'code', 'instruct')):
            continue

        # Recency check
        last_mod = item.get('lastModified')
        last_mod_ts = 0.0
        if last_mod:
            try:
                dt_obj = datetime.fromisoformat(last_mod.replace('Z', '+00:00'))
                last_mod_ts = dt_obj.timestamp()
            except Exception:
                last_mod_ts = 0.0
        if last_mod_ts > 0 and (now - last_mod_ts) > (max_age_days * 86400):
            continue

        params = parse_param_count(repo_id)
        size_bytes = estimate_q4_km_size_bytes(params) if params else None
        size_gb = round(size_bytes / (1024**3), 2) if size_bytes else None
        fits = model_fits_threshold(params) if params else False
        publisher = repo_id.split('/')[0] if '/' in repo_id else ''

        parsed.append({
            'repo': repo_id,
            'publisher': publisher,
            'last_modified': last_mod,
            'last_modified_ts': last_mod_ts,
            'params': params,
            'size_bytes': size_bytes,
            'size_gb': size_gb,
            'fits': fits,
        })
    return parsed


def load_tested_models(path: Path = None) -> dict:
    """Load dictionary of previously tested models from JSON file."""
    p = Path(path) if path else TESTED_MODELS_DEFAULT
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def record_tested_model(repo: str, verdict: str = None, summary_file: str = None, path: Path = None) -> None:
    """Record a tested model and its verdict to tested_models.json."""
    p = Path(path) if path else TESTED_MODELS_DEFAULT
    data = load_tested_models(p)
    data[repo] = {
        'tested_at': datetime.now(timezone.utc).isoformat(),
        'verdict': verdict,
        'summary_file': str(summary_file) if summary_file else None,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temp, p)


def resolve_hf_candidate(candidate_data: dict | str, fetch: bool = False) -> dict:
    """Extract exact metadata (repo, file, revision sha, byte_size, ngl) from HF model data.
    Strictly refuses invented metadata (no 'pinned_default', no 2.5 GB placeholder).
    Raises ValueError if revision hash or byte_size cannot be resolved.
    """
    if isinstance(candidate_data, str):
        repo_id = candidate_data
        if fetch:
            url = f"https://huggingface.co/api/models/{repo_id}?blobs=true"
            req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
            except Exception as e:
                raise ValueError(f"Failed to fetch metadata for {repo_id}: {e}")
        else:
            raise ValueError(f"Cannot resolve metadata for string '{repo_id}' without fetch=True or explicit metadata dict")
    elif isinstance(candidate_data, dict):
        data = candidate_data
        repo_id = data.get('repo') or data.get('id')
    else:
        raise ValueError(f"Invalid candidate data type: {type(candidate_data)}")

    if not repo_id:
        raise ValueError("Missing repo identifier in candidate data")

    sha = data.get('sha') or data.get('revision')
    if not sha or not isinstance(sha, str) or len(sha) < 8 or sha in ('pinned_default', 'unknown'):
        raise ValueError(f"Refusing candidate {repo_id}: pinned revision hash missing or invalid")

    file_name = data.get('file')
    byte_size = data.get('byte_size') or data.get('size')

    if not file_name or not byte_size:
        siblings = data.get('siblings', [])
        # Look for Q4_K_M GGUF file
        q4_siblings = [
            s for s in siblings
            if isinstance(s, dict) and s.get('rfilename', '').lower().endswith('.gguf') and 'q4_k_m' in s.get('rfilename', '').lower()
        ]
        if not q4_siblings:
            # Fall back to any Q4 GGUF
            q4_siblings = [
                s for s in siblings
                if isinstance(s, dict) and s.get('rfilename', '').lower().endswith('.gguf') and 'q4' in s.get('rfilename', '').lower()
            ]
        if q4_siblings and q4_siblings[0].get('size'):
            file_name = q4_siblings[0]['rfilename']
            byte_size = q4_siblings[0]['size']

    if not file_name or not byte_size or int(byte_size) <= 0:
        raise ValueError(f"Refusing candidate {repo_id}: Q4_K_M GGUF file or byte size missing or invalid")

    byte_size = int(byte_size)
    param_count = parse_param_count(file_name) or parse_param_count(repo_id) or 0.0

    # Fit filter NGL calculation: largest fitting ngl
    if param_count <= 2e9 or byte_size <= 1800000000:
        ngl = 28
    elif param_count <= 4.5e9 or byte_size <= 3800000000:
        ngl = 24
    else:
        ngl = 10

    candidate_id = data.get('id_clean') or file_name.replace('.gguf', '').lower()
    return {
        'id': candidate_id,
        'repo': repo_id,
        'file': file_name,
        'revision': sha,
        'byte_size': byte_size,
        'ngl': data.get('ngl', ngl),
        'param_count': param_count,
        'size_gb': round(byte_size / 1e9, 2),
    }


def pick_best_candidate(candidates: list, tested_repos: set = None, tested_models_path: Path = None, graph_path: Path = None) -> dict | None:
    """Pick at most one best candidate per week from models fitting <= 6 GB not already tested or declined."""
    if tested_repos is None:
        tested_dict = load_tested_models(tested_models_path)
        tested_repos = set(tested_dict.keys())

    declined = graph.get_declined_models(graph_path or GRAPH_DB)

    qualifying = [
        c for c in candidates
        if c.get('fits')
        and c.get('repo') not in tested_repos
        and not graph.is_candidate_declined(c, declined)
    ]
    if not qualifying:
        return None

    def candidate_score(c):
        repo_lower = c['repo'].lower()
        coder_score = 20 if 'coder' in repo_lower else (10 if 'code' in repo_lower else 5)
        param_capacity = c.get('params') or 0.0
        recency = c.get('last_modified_ts') or 0.0
        return (coder_score, param_capacity, recency)

    qualifying.sort(key=candidate_score, reverse=True)
    return qualifying[0]


def fetch_hf_publisher_models(publisher: str, timeout: int = 3) -> list:
    """Fetch GGUF models for a given publisher via read-only Hugging Face API."""
    import urllib.request
    url = f"https://huggingface.co/api/models?author={publisher}&search=gguf&blobs=true"
    req = urllib.request.Request(url, headers={'User-Agent': 'selfimprove/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def models_scan(fetch: bool = True, fetcher=None, fixture_path=None, hub_fixtures_path=None, advancements_path=None, tested_models_path=None, now: float = None, graph_path: Path = None) -> dict:
    """Scan Hugging Face and 20 model hubs for new GGUF coder models, appending to advancements.jsonl.
    Network failures are recorded and never fatal.
    """
    advancements_path = Path(advancements_path) if advancements_path else ADVANCEMENTS
    now = time.time() if now is None else now

    raw_items = []
    network_errors = []

    if fixture_path:
        fix = Path(fixture_path)
        if fix.exists():
            raw_items = json.loads(fix.read_text(encoding='utf-8'))
    elif fetch:
        fetcher = fetcher or fetch_hf_publisher_models
        for pub in HF_PUBLISHERS:
            try:
                res = fetcher(pub)
                if isinstance(res, list):
                    raw_items.extend(res)
            except Exception as exc:
                err_entry = {
                    'at': int(now),
                    'tool': 'hf-models',
                    'type': 'network_error',
                    'publisher': pub,
                    'error': str(exc),
                }
                network_errors.append(err_entry)
                try:
                    advancements_path.parent.mkdir(parents=True, exist_ok=True)
                    with advancements_path.open('a') as f:
                        f.write(json.dumps(err_entry) + '\n')
                except OSError:
                    pass

    parsed = parse_hf_models(raw_items, max_age_days=7, now=now)

    # Multi-hub scanning across 20 hubs
    hub_fixtures = None
    if hub_fixtures_path:
        hp = Path(hub_fixtures_path)
        if hp.exists():
            hub_fixtures = json.loads(hp.read_text(encoding='utf-8'))
    elif fixture_path:
        if HUB_FIXTURES_DEFAULT.exists():
            try:
                hub_fixtures = json.loads(HUB_FIXTURES_DEFAULT.read_text(encoding='utf-8'))
            except Exception:
                hub_fixtures = None

    hub_res = ha.scan_all_hubs(
        fetch=fetch,
        fixtures=hub_fixtures,
        per_source_timeout=10.0,
        total_scan_cap=120.0,
        now=now,
    )

    # Merge cross-hub Tier 1 candidates with parsed HF candidates
    combined_seen = list(parsed)
    seen_canonical = {ha.normalize_canonical_key(c['repo']): c for c in combined_seen}

    for hc in hub_res.get('candidates', []):
        canon = hc['canonical_id']
        if canon in seen_canonical:
            existing = seen_canonical[canon]
            hubs = existing.setdefault('hubs_seen', [existing.get('publisher', 'huggingface')])
            for h in hc.get('hubs_seen', []):
                if h not in hubs:
                    hubs.append(h)
        else:
            new_cand = {
                'repo': hc['repo'],
                'publisher': hc.get('primary_hub', 'other'),
                'last_modified': None,
                'last_modified_ts': None,
                'params': hc.get('params'),
                'size_bytes': hc.get('size_bytes'),
                'size_gb': round(hc.get('size_bytes', 0) / 1e9, 2),
                'fits': hc.get('fits', False),
                'is_gguf': hc.get('is_gguf', False),
                'hubs_seen': hc.get('hubs_seen', [hc.get('primary_hub')]),
            }
            combined_seen.append(new_cand)
            seen_canonical[canon] = new_cand

    tested_dict = load_tested_models(tested_models_path)
    tested_repos = set(tested_dict.keys())
    best = pick_best_candidate(combined_seen, tested_repos=tested_repos, tested_models_path=tested_models_path, graph_path=graph_path or GRAPH_DB)

    # Attach raw item metadata to parsed candidates where available
    raw_by_id = {item.get('id'): item for item in raw_items if isinstance(item, dict) and item.get('id')}
    best_resolved = None
    if best and best['repo'] in raw_by_id:
        try:
            best_resolved = resolve_hf_candidate(raw_by_id[best['repo']], fetch=False)
        except Exception:
            pass
    elif best:
        best_resolved = best

    prev_advancements = read_jsonl(advancements_path) if advancements_path.exists() else []
    prev_repos = {a.get('repo') for a in prev_advancements if a.get('repo')}

    for c in combined_seen:
        if c['repo'] not in prev_repos:
            entry = {
                'at': int(now),
                'tool': 'hf-models',
                'type': 'model_candidate',
                'repo': c['repo'],
                'publisher': c.get('publisher'),
                'params': c.get('params'),
                'size_gb': c.get('size_gb'),
                'fits': c.get('fits'),
                'hubs_seen': c.get('hubs_seen', []),
                'best_candidate': (best is not None and c['repo'] == best['repo']),
                'last_modified': c.get('last_modified'),
            }
            try:
                advancements_path.parent.mkdir(parents=True, exist_ok=True)
                with advancements_path.open('a') as f:
                    f.write(json.dumps(entry) + '\n')
            except OSError:
                pass
            prev_repos.add(c['repo'])

    return {
        'seen': combined_seen,
        'qualifying': [c for c in combined_seen if c.get('fits')],
        'best_candidate': best,
        'best_resolved': best_resolved,
        'network_errors': network_errors,
        'tested_count': len(tested_repos),
        'hub_sources': hub_res.get('sources', []),
        'tier2_hits': hub_res.get('tier2_hits', []),
    }


def evaluate_model_verdict(candidate: dict, baseline: dict) -> tuple[str, str]:
    """Evaluate candidate vs baseline according to standing rules:
    - replace: never slower on any measured item and more correct
    - fallback-only: more correct but slower on some measured items
    - no: not more correct, or slower without accuracy win
    """
    c_qual = candidate.get('quality_pass_count', 0)
    b_qual = baseline.get('quality_pass_count', 0)
    c_total = candidate.get('quality_total', 20)
    b_total = baseline.get('quality_total', 20)

    more_correct = c_qual > b_qual

    c_gen = candidate.get('avg_gen_tok_s', candidate.get('avg_generation_tok_s', 0.0))
    b_gen = baseline.get('avg_gen_tok_s', baseline.get('avg_generation_tok_s', 0.0))
    c_ttft = candidate.get('avg_ttft_s', float('inf'))
    b_ttft = baseline.get('avg_ttft_s', float('inf'))
    c_wall = candidate.get('avg_cases_wall_s', float('inf'))
    b_wall = baseline.get('avg_cases_wall_s', float('inf'))

    never_slower = (c_gen >= b_gen) and (c_ttft <= b_ttft) and (c_wall <= b_wall)

    if more_correct and never_slower:
        verdict = 'replace'
        reason = f"More correct ({c_qual}/{c_total} vs {b_qual}/{b_total}) and never slower on any measured item."
    elif more_correct:
        verdict = 'fallback-only'
        reason = f"More correct ({c_qual}/{c_total} vs {b_qual}/{b_total}) but slower on measured speed items (gen: {c_gen:.2f} vs {b_gen:.2f} tok/s, TTFT: {c_ttft:.3f}s vs {b_ttft:.3f}s)."
    else:
        verdict = 'no'
        reason = f"Not more correct ({c_qual}/{c_total} vs {b_qual}/{b_total})."
    return verdict, reason


def is_model_declined(candidate_meta: dict, graph_path: Path = None, tested_models_path: Path = None, proposals_path: Path = None) -> bool:
    """Check if model candidate has a recorded decline decision across graph, tested_models, or proposals."""
    g_path = Path(graph_path) if graph_path else GRAPH_DB
    if g_path.exists():
        declined_set = graph.get_declined_models(g_path)
        if graph.is_candidate_declined(candidate_meta, declined_set):
            return True

    repo = candidate_meta.get('repo', '')
    name = candidate_meta.get('file', repo.split('/')[-1]).lower()
    clean_name = repo.split('/')[-1].lower().replace('.gguf', '')

    # Check tested_models.json ONLY if explicitly declined
    t_path = Path(tested_models_path) if tested_models_path else TESTED_MODELS_DEFAULT
    tested = load_tested_models(t_path)
    for t_repo, t_info in tested.items():
        t_clean = t_repo.split('/')[-1].lower().replace('.gguf', '')
        if t_repo.lower() == repo.lower() or t_clean in (name, clean_name):
            verdict = (t_info.get('verdict') or '').lower()
            if verdict in ('declined', 'rejected', 'no'):
                return True

    # Check proposals.jsonl decisions
    prop_file = Path(proposals_path) if proposals_path else PROPOSALS
    if prop_file.exists():
        events = read_jsonl(prop_file)
        proposals, decisions = proposal_state(events)
        for idx, dec in decisions.items():
            dec_type = dec.get('decision', '').lower()
            dec_note = str(dec.get('note', '')).lower()
            if dec_type in ('rejected', 'declined') or 'declined' in dec_note:
                if 1 <= idx <= len(proposals):
                    prop = proposals[idx - 1]
                    idea_lower = prop.get('idea', '').lower()
                    tool_lower = str(prop.get('tool', '')).lower()
                    repo_lower = repo.lower()
                    if repo_lower in idea_lower or repo_lower in tool_lower or clean_name in idea_lower or clean_name in tool_lower:
                        return True

    return False


def models_test(
    candidate: dict | str = None,
    dry_run: bool = False,
    manifest_path: Path = None,
    candidate_config_path: Path = None,
    logs_dir: Path = None,
    fixture_summary: Path = None,
    graph_path: Path = None,
    tested_models_path: Path = None,
    proposals_path: Path = None,
) -> dict:
    """Queue one idle-gated A/B job that reuses bench2 run-abtest vs production 3B.
    Configures candidate (repo, file, pinned revision, byte size) and writes manifest.
    In dry-run mode, executes bench2 runner with --dry-run and returns per-candidate summary.
    If the candidate is already declined (recorded decision), testing is skipped.
    """
    manifest_path = Path(manifest_path) if manifest_path else BENCH2_DOWNLOADS_DEFAULT
    candidate_config_path = Path(candidate_config_path) if candidate_config_path else BENCH2_CANDIDATE_CONFIG_DEFAULT
    logs_dir = Path(logs_dir) if logs_dir else BENCH_LOGS_DEFAULT
    models_dir = MODELS_DIR_DEFAULT
    graph_path = Path(graph_path) if graph_path else GRAPH_DB
    tested_models_path = Path(tested_models_path) if tested_models_path else TESTED_MODELS_DEFAULT
    proposals_path = Path(proposals_path) if proposals_path else PROPOSALS

    if candidate is None:
        candidate = {
            'id': 'deepseek-coder-1.3b',
            'repo': 'deepseek-ai/DeepSeek-Coder-1.3B-GGUF',
            'file': 'DeepSeek-Coder-1.3B-Q4_K_M.gguf',
            'revision': 'bc640142c66e1fdd12af0bd68f40445458f3869b',
            'byte_size': 1234567890,
        }

    candidate_meta = resolve_hf_candidate(candidate)
    repo = candidate_meta['repo']

    if is_model_declined(candidate_meta, graph_path=graph_path, tested_models_path=tested_models_path, proposals_path=proposals_path):
        return {
            'queued': False,
            'declined': True,
            'dry_run': dry_run,
            'candidate': candidate_meta,
            'reason': f"Candidate {repo} already declined (recorded decision); skipping re-test.",
        }
    repo_dir = repo.split('/')[-1]
    filename = candidate_meta['file']
    dest_path = models_dir / repo_dir / filename

    # Write candidate config for bench2 run-abtest
    try:
        candidate_config_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_config_path.write_text(json.dumps(candidate_meta, indent=2) + '\n', encoding='utf-8')
    except OSError:
        pass

    # Record manifest in bench2-downloads.json
    manifest_data = {}
    if manifest_path.exists():
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding='utf-8'))
        except Exception:
            manifest_data = {}

    manifest_data[str(dest_path)] = {
        'repo': repo,
        'file': filename,
        'pinned_revision_hash': candidate_meta['revision'],
        'byte_size': candidate_meta['byte_size'],
    }

    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest_data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except OSError:
        pass

    # Queue via jp-guard queue add-exec run-abtest -- /home/chopp/local-llm/bench2/run-abtest.sh
    cmd = ['jp-guard', 'queue', 'add-exec', 'run-abtest', '--', str(BENCH2_ABTEST_SCRIPT)]

    summary_file = None
    summary_data = None
    if dry_run:
        # Execute dry-run with bench2 head-to-head runner
        if BENCH2_HEAD_TO_HEAD_SCRIPT.exists():
            runner_cmd = [
                sys.executable,
                str(BENCH2_HEAD_TO_HEAD_SCRIPT),
                '--dry-run',
                '--repo', repo,
                '--file', filename,
                '--revision', candidate_meta['revision'],
                '--byte-size', str(candidate_meta['byte_size']),
                '--ngl', str(candidate_meta['ngl']),
            ]
            subprocess.run(runner_cmd, capture_output=True, text=True)
        cand_id = candidate_meta['id']
        cand_summary_path = logs_dir / f"{cand_id}_summary.json"
        if cand_summary_path.exists():
            try:
                summary_data = json.loads(cand_summary_path.read_text(encoding='utf-8'))
                summary_file = str(cand_summary_path)
            except Exception:
                pass
        queued = True
    else:
        p = subprocess.run(cmd, capture_output=True, text=True)
        queued = (p.returncode == 0)

    return {
        'queued': queued,
        'dry_run': dry_run,
        'candidate': candidate_meta,
        'dest': str(dest_path),
        'manifest': str(manifest_path),
        'command': cmd,
        'summary_file': summary_file,
        'summary_data': summary_data,
    }


def new_coder_models_lines(seen: list = None, candidate: dict = None, baseline: dict = None, verdict: str = None, reason: str = None, days: int = 7, hub_sources: list = None, tier2_hits: list = None) -> list:
    """Format weekly review section 'New coder models'."""
    lines = ['', '## New coder models', '']

    # Model Hubs Scanned table
    if hub_sources:
        lines.append('### Model Hubs Scanned')
        lines.append('| Hub | Tier | Status | Hits | Notes |')
        lines.append('|---|---|---|---|---|')
        for s in hub_sources:
            status = 'checked' if s.get('checked') else 'skipped'
            hits_n = len(s.get('hits', []))
            note = s.get('skipped_reason') or s.get('error') or f"{hits_n} hits"
            lines.append(f"| {s.get('display_name', s.get('hub'))} | Tier {s.get('tier')} | {status} | {hits_n} | {note} |")
        lines.append('')

    if tier2_hits:
        lines.append('### Tier 2 Hub Hits')
        for h in tier2_hits:
            lines.append(f"- {h.get('hub')}: {h.get('name')} (record only, never downloaded)")
        lines.append('')

    lines.append('### Seen')
    if seen:
        for s in seen:
            fit_label = 'fits (<= 6 GB)' if s.get('fits') else 'exceeds 6 GB'
            hubs = s.get('hubs_seen', [])
            hubs_str = f" [seen on: {', '.join(hubs)}]" if hubs else ""
            lines.append(f"- {s.get('repo')} ({s.get('size_gb', '?')} GB) - {fit_label}{hubs_str}")
    else:
        lines.append('- None recorded in last scan window.')

    lines.append('')
    lines.append('### Tested')
    if candidate:
        c_name = candidate.get('name') or candidate.get('repo') or 'Candidate'
        lines.append(f"- Candidate: {c_name} ({candidate.get('size_gb', '?')} GB, ngl={candidate.get('ngl', '?')})")
        if baseline:
            b_name = baseline.get('name') or 'Qwen2.5-Coder-3B (Production Baseline)'
            lines.append(f"- Baseline: {b_name} ({baseline.get('size_gb', '?')} GB, ngl={baseline.get('ngl', '?')})")
    else:
        lines.append('- No model tested this period.')

    lines.append('')
    lines.append('### Numbers')
    if candidate and baseline:
        lines.append('| Model | Size (GB) | GPU Layers (`-ngl`) | Gen tok/s | TTFT (s) | Quality Pass Rate |')
        lines.append('|---|---|---|---|---|---|')
        for m in (baseline, candidate):
            q_str = f"{m.get('quality_pass_count', '?')}/{m.get('quality_total', 20)}"
            gen_tok = m.get('avg_gen_tok_s', m.get('avg_generation_tok_s', '?'))
            lines.append(f"| {m.get('name', 'Model')} | {m.get('size_gb', '?')} | {m.get('ngl', '?')} | {gen_tok} | {m.get('avg_ttft_s', '?')} | {q_str} |")
    else:
        lines.append('No benchmark numbers available.')

    lines.append('')
    lines.append('### Verdict')
    if verdict:
        lines.append(f"- **{verdict}**: {reason or ''}")
    else:
        lines.append('- No candidate qualified for A/B trial.')
    lines.append('- *Policy notice: Never change the default model or qwen-server settings; that is always the captain\'s call.*')

    return lines


def cmd_models_scan(a):
    fetch_flag = getattr(a, 'fetch', True)
    if getattr(a, 'fixture', None):
        fetch_flag = False
    hub_fix = getattr(a, 'hub_fixtures', None)
    graph_p = getattr(a, 'graph', None) or getattr(a, 'db', None)
    tested_p = getattr(a, 'tested_models', None)
    res = models_scan(
        fetch=fetch_flag,
        fixture_path=a.fixture,
        hub_fixtures_path=hub_fix,
        advancements_path=a.advancements,
        tested_models_path=tested_p,
        graph_path=graph_p,
    )
    print(f"Models scan complete. Total seen: {len(res['seen'])}, Qualifying (<= 6 GB): {len(res['qualifying'])}")
    if res.get('hub_sources'):
        checked_count = sum(1 for s in res['hub_sources'] if s.get('checked'))
        print(f"Hubs scanned: {checked_count}/20 checked ({len(res['hub_sources']) - checked_count} skipped)")
    for m in res['seen']:
        mark = " [BEST CANDIDATE]" if res['best_candidate'] and m['repo'] == res['best_candidate']['repo'] else ""
        fit = "FITS" if m['fits'] else "REJECTED (> 6 GB)"
        hubs = f" [seen on: {', '.join(m.get('hubs_seen', []))}]" if m.get('hubs_seen') else ""
        print(f"  - {m['repo']} ({m.get('size_gb', '?')} GB, {fit}){mark}{hubs}")
    if res['network_errors']:
        print(f"Network errors ({len(res['network_errors'])}):")
        for err in res['network_errors']:
            print(f"  - {err['publisher']}: {err['error']}")
    return 0


def cmd_models_test(a):
    candidate = a.candidate
    if getattr(a, 'repo', None) and getattr(a, 'file', None) and getattr(a, 'revision', None) and getattr(a, 'byte_size', None):
        candidate = {
            'repo': a.repo,
            'file': a.file,
            'revision': a.revision,
            'byte_size': a.byte_size,
        }
    graph_path = getattr(a, 'graph', None) or getattr(a, 'db', None)
    tested_models_path = getattr(a, 'tested_models', None)
    proposals_path = getattr(a, 'proposals', None)
    res = models_test(
        candidate=candidate,
        dry_run=a.dry_run,
        manifest_path=a.manifest,
        fixture_summary=a.fixture,
        graph_path=graph_path,
        tested_models_path=tested_models_path,
        proposals_path=proposals_path,
    )
    if res.get('declined'):
        print(f"Skipped: {res.get('reason')}")
        return 0
    if res['dry_run']:
        print(f"Dry-run: candidate A/B job prepared for {res['candidate'].get('repo')}.")
        print(f"  Manifest recorded: {res['manifest']}")
        print(f"  Queue command: {' '.join(res['command'])}")
        if res.get('summary_file'):
            print(f"  Summary file: {res['summary_file']}")
    else:
        status = 'Queued successfully' if res['queued'] else 'Failed to queue'
        print(f"{status}: {' '.join(res['command'])}")
    return 0


def cmd_scan(a):
    res = scan(fetch=a.fetch, watched_path=a.watched)
    if not a.fetch:
        print(f"Watched tools ({len(res['tools'])}):")
        for t in res['tools']:
            src = t.get('command') or t.get('changelog_url')
            print(f"  - {t['name']}: {src}")
        print("Run 'si.py scan --fetch' to opt in to checking upstream tools.")
    else:
        print(f"Scan complete. New advancements: {len(res['new_items'])}")
        for item in res['new_items']:
            print(f"  - {item['tool']}: {item['item']} (proposal #{item['proposal']})")


def review_build_graph(concepts_path=None):
    """Build knowledge graph for weekly review."""
    if concepts_path is None and CONCEPTS.exists():
        concepts_path = CONCEPTS
    try:
        return graph.build_graph(
            db_path=GRAPH_DB,
            concepts_path=concepts_path,
            records_path=RECORDS,
            proposals_path=PROPOSALS,
            advancements_path=ADVANCEMENTS,
            tokens_path=TOKENS,
        )
    except (OSError, sqlite3.OperationalError):
        return None


def cmd_review(a):
    if TUNABLES.exists():
        tune()
    s = summarize(load(a.days))
    props = propose(s)
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    REPORTS.mkdir(exist_ok=True)
    lines = [f'# Weekly self-improvement review {day}', '', '## Measured', '']
    lines += [f'- {k}: {v}' for k, v in s.items()]
    lines += ['', '## Proposals (need the captain\'s OK)', '']
    lines += [f'{i}. {p["change"]}. Gain: {p["gain"]}. Risk: {p["risk"]}. Proof next week: {p["check"]}.'
              for i, p in enumerate(props, 1)] or ['None this week.']
    findings = load_findings(days=a.days)
    lines += proposal_log_lines()
    lines += trials_lines()
    lines += goal_scorecard_lines(load(a.days), read_jsonl(PROPOSALS), findings=findings)
    lines += leak_watch_lines(findings)
    lines += bgapps_sweep_lines(
        inventory_output=getattr(a, 'bgapps_synthetic', None),
        decisions_path=getattr(a, 'bgapps_decisions', None),
        script_path=getattr(a, 'bgapps_inventory', None),
    )
    lines += loop_health_lines()
    lines += charter_gap_lines()
    lines += tuning_lines(a.days)
    lines += learned_limits_lines(load(a.days), read_jsonl(RECORDS) if RECORDS.exists() else [], days=a.days)
    lines += qwen_job_size_lines(getattr(a, 'driver_results', DRIVER_RESULTS_DEFAULT))

    # Build graph so review and candidate selection consult fresh knowledge graph
    review_build_graph(concepts_path=getattr(a, 'concepts', None))

    # Weekly coder models section: fetch fresh models from Hugging Face
    scan_res = models_scan(fetch=True)
    best_candidate = scan_res.get('best_resolved') or scan_res.get('best_candidate')

    # Automatically queue models-test for best untested candidate
    if best_candidate:
        try:
            models_test(candidate=best_candidate, dry_run=False)
        except Exception:
            pass

    # Look for per-candidate summary file in bench-logs
    logs_dir = BENCH_LOGS_DEFAULT
    candidate_summary = None
    baseline_summary = None
    verdict = None
    reason = None

    summary_files = sorted(logs_dir.glob('*_summary.json'), key=lambda p: p.stat().st_mtime, reverse=True) if logs_dir.exists() else []
    if summary_files:
        try:
            bench_data = json.loads(summary_files[0].read_text(encoding='utf-8'))
            candidate_summary = bench_data.get('candidate')
            baseline_summary = bench_data.get('baseline')
            verdict = bench_data.get('verdict')
            reason = bench_data.get('reason')
        except Exception:
            pass

    lines += new_coder_models_lines(
        seen=scan_res.get('seen'),
        candidate=candidate_summary,
        baseline=baseline_summary,
        verdict=verdict,
        reason=reason,
        days=a.days,
        hub_sources=scan_res.get('hub_sources'),
        tier2_hits=scan_res.get('tier2_hits'),
    )

    token_stats = aggregate_weekly_tokens(days=a.days, prev_totals_path=PREV_TOKENS)
    if token_stats.get('all_records'):
        try:
            TOKENS.write_text('\n'.join(json.dumps(r) for r in token_stats['all_records']) + '\n', encoding='utf-8')
        except OSError:
            pass
    lines += token_report_lines(token_stats)
    lines += new_concepts_lines(CONCEPTS, days=a.days)
    review_build_graph(concepts_path=getattr(a, 'concepts', None))
    graph_summary = graph.graph_size_summary(GRAPH_DB)
    print(graph_summary)
    lines += ['', '## Knowledge graph memory', '', graph_summary]

    path = REPORTS / f'{day}.md'
    path.write_text('\n'.join(lines) + '\n')
    notify_review_ready(path)
    reset_weekly_token_records(TOKENS, prev_totals_path=PREV_TOKENS, current_totals=token_stats['per_cli'])
    print(path)


def cmd_tokens(a):
    prev_totals = getattr(a, 'prev_totals', PREV_TOKENS)
    stats = aggregate_weekly_tokens(days=a.days, prev_totals_path=prev_totals)
    if not getattr(a, 'dry_run', False) and stats.get('all_records'):
        try:
            TOKENS.write_text('\n'.join(json.dumps(r) for r in stats['all_records']) + '\n', encoding='utf-8')
        except OSError:
            pass
    lines = token_report_lines(stats)
    print('\n'.join(lines))
    return 0


def cmd_graph(a):
    if a.graph_cmd == 'build':
        concepts = getattr(a, 'concepts', None)
        if concepts is None and CONCEPTS.exists():
            concepts = CONCEPTS
        aliases = getattr(a, 'aliases', None)
        res = graph.build_graph(db_path=getattr(a, 'db', None), concepts_path=concepts, aliases_path=aliases)
        print(f"built graph: {res['facts']} facts, {res['concepts']} concepts")
        return 0
    elif a.graph_cmd == 'query':
        aliases = getattr(a, 'aliases', None)
        db_p = getattr(a, 'db', None)
        is_private = getattr(a, 'private', False)
        try:
            facts = graph.query_graph(
                a.subject,
                relation=getattr(a, 'relation', None),
                db_path=db_p,
                aliases_path=aliases,
                private=is_private,
            )
        except PermissionError as e:
            print(f"refused: {e}")
            return 1
        for f in facts:
            print(graph.format_fact_line(f))

        if getattr(a, 'related', False):
            tunables_p = getattr(a, 'tunables', None)
            threshold = load_similarity_threshold(tunables_p)
            try:
                candidates = graph.get_graph_search_candidates(db_path=db_p, private=is_private)
                embedder = getattr(a, '_embedder', None)
                if embedder is not None:
                    related = graph.find_related_items(
                        a.subject,
                        candidates,
                        embedder,
                        db_path=db_p,
                        threshold=threshold,
                        top_k=5,
                        exact_match_filter=a.subject,
                    )
                else:
                    with graph.LlamaServerEmbedder() as emb:
                        related = graph.find_related_items(
                            a.subject,
                            candidates,
                            emb,
                            db_path=db_p,
                            threshold=threshold,
                            top_k=5,
                            exact_match_filter=a.subject,
                        )
            except PermissionError as e:
                print(f"refused: {e}")
                return 1
            except Exception as e:
                print(f"vector search offline: {e}; falling back to exact match")
                related = []

            for cand, score in related:
                print(f"possibly related: {cand} (score: {score:.2f})")
                r_facts = graph.query_graph(
                    cand,
                    relation=getattr(a, 'relation', None),
                    db_path=db_p,
                    aliases_path=aliases,
                    private=is_private,
                )
                for f in r_facts:
                    print(f"  {graph.format_fact_line(f)}")

            if related:
                top_cand, top_score = related[0]
                log_hint(
                    a.subject,
                    top_cand,
                    top_score,
                    source='query',
                    hints_path=getattr(a, 'hints', None),
                )
        return 0
    return 1


def cmd_noop_measure(a):
    transcripts = getattr(a, 'transcripts', []) or []
    days = getattr(a, 'days', None)
    res = noop.measure_transcripts(transcripts, days=days)
    if getattr(a, 'json', False):
        print(json.dumps(res, indent=2))
    else:
        print(noop.format_measurement_report(res))
    return 0


def cmd_bgapps(a):
    lines = bgapps_sweep_lines(
        inventory_output=getattr(a, 'synthetic', None),
        decisions_path=getattr(a, 'decisions', None),
        script_path=getattr(a, 'inventory', None),
    )
    print('\n'.join(lines).strip())
    return 0


def notify_review_ready(path: Path) -> None:
    """Wake master Claude through the firstmate inbox so the review reaches the captain.
    Can be disabled or redirected via FM_NOTIFY_DISABLE=1, SELFIMPROVE_TESTING=1, or FM_INBOX_STUB.
    """
    if os.environ.get('FM_NOTIFY_DISABLE') == '1' or os.environ.get('SELFIMPROVE_TESTING') == '1':
        return
    stub_path = os.environ.get('FM_INBOX_STUB')
    if stub_path:
        Path(stub_path).write_text(f"Weekly self-improvement review ready: {path}\n", encoding='utf-8')
        return
    inbox_script = FM / 'bin' / 'fm-inbox.sh'
    if inbox_script.exists():
        subprocess.run([str(inbox_script), 'note', f'Weekly self-improvement review ready: {path}'],
                       env={**os.environ, 'FM_HOME': str(FM)}, capture_output=True, check=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('record')
    r.add_argument('--task', required=True); r.add_argument('--master', required=True)
    r.add_argument('--labour', required=True); r.add_argument('--outcome', choices=['done', 'failed'], required=True)
    r.add_argument('--quota-before', type=float); r.add_argument('--quota-after', type=float)
    r.add_argument('--minutes', type=float); r.add_argument('--checks'); r.add_argument('--blocker', action='append')
    r.add_argument('--tool', default='qwen', help='Tool name (default: qwen)')
    r.add_argument('--reverse', action='store_true', help='Record a reverse-check measurement with old value')
    r.add_argument('--benchmark', type=nonblank, help='Tunable name; tag a frozen benchmark measurement')
    r.add_argument('--case', type=nonblank, help='Case ID from that tunable\'s test-set manifest')
    r.add_argument('--learned', help='Why / limit learned from this task outcome')
    sub.add_parser('tune', help='Evaluate or undo one trial; start at most one approved step per UTC week',
                   description='No flags. Requires approved bounds, a local_json setting, and a frozen test manifest.',
                   epilog='Examples: si.py tune; si.py review (also runs tune). See README.md for approvals and benchmark records.')
    v = sub.add_parser('review'); v.add_argument('--days', type=int, default=7)
    v.add_argument('--driver-results', type=Path, default=DRIVER_RESULTS_DEFAULT,
                   help='Path to driver results directory')
    v.add_argument('--concepts', type=Path, default=None,
                   help='Path to concepts glossary file')
    v.add_argument('--bgapps-inventory', type=Path, default=None,
                   help='Path to bgapps-inventory.ps1')
    v.add_argument('--bgapps-decisions', type=Path, default=None,
                   help='Path to bgapps-decisions.md')
    v.add_argument('--bgapps-synthetic', default=None,
                   help='Synthetic inventory output for testing')
    p = sub.add_parser('propose', help='Append an idea for the captain to review',
                       epilog='Example: si.py propose --idea \"Split tasks\" --why \"Reduce retries\"; then si.py review')
    p.add_argument('--idea', type=nonblank, required=True)
    p.add_argument('--why', type=nonblank, required=True)
    for name in ('gain', 'risk', 'check'):
        p.add_argument('--' + name, type=nonblank, default='not specified')
    p.add_argument('--source', type=nonblank, default='agent')
    p.add_argument('--goal', type=nonblank, help='Charter goal this proposal targets')
    p.add_argument('--tool', type=nonblank, help='Tool this proposal targets')
    p.add_argument('--hints', type=Path, default=None, help='Path to hints.jsonl')
    p.add_argument('--tunables', type=Path, default=None, help='Path to tunables.json')
    p.add_argument('--graph', '--db', dest='db', type=Path, default=None, help='Path to graph SQLite database')
    p.add_argument('--proposals', type=Path, default=None, help='Path to proposals.jsonl')
    d = sub.add_parser('decide', help="Append the captain's decision; indexes appear in review",
                       epilog='Example: si.py decide 1 later --note "Measure first"')
    d.add_argument('index', type=int)
    d.add_argument('decision', choices=['adopted', 'rejected', 'later'])
    d.add_argument('--note', type=nonblank)
    d.add_argument('--trial-measure', '--trial', '--measure', dest='trial_measure', type=nonblank,
                   help='Trial measure for adopted proposals (R18)')
    d.add_argument('--trial-date', dest='trial_date', type=nonblank,
                   help='Review date (YYYY-MM-DD); default: decision date + 7 days')
    d.add_argument('--verdict', choices=['keep', 'clean up', 'pending'], help='Trial verdict')
    sc = sub.add_parser('scan', help='Scan watched tools for upstream advancements')
    sc.add_argument('--fetch', action='store_true', help='Opt-in: fetch upstream versions or changelogs')
    sc.add_argument('--watched', help='Path to watched tools JSON')
    msc = sub.add_parser('models-scan', help='Scan Hugging Face for new GGUF coder models')
    msc.add_argument('--fetch', action='store_true', default=None, help='Fetch fresh models from Hugging Face API')
    msc.add_argument('--fixture', help='Path to fixture JSON for testing without network')
    msc.add_argument('--hub-fixtures', help='Path to multi-hub fixtures JSON for testing without network')
    msc.add_argument('--advancements', help='Path to advancements JSONL file')
    msc.add_argument('--graph', '--db', dest='graph', help='Path to knowledge graph sqlite database')
    msc.add_argument('--tested-models', help='Path to tested_models.json file')
    mt = sub.add_parser('models-test', help='Queue idle-gated A/B test for candidate coder model')
    mt.add_argument('--candidate', help='Candidate model repo or JSON')
    mt.add_argument('--repo', help='Candidate model repo')
    mt.add_argument('--file', help='Candidate GGUF file name')
    mt.add_argument('--revision', help='Candidate pinned revision hash')
    mt.add_argument('--byte-size', type=int, help='Candidate file size in bytes')
    mt.add_argument('--dry-run', action='store_true', help='Simulate test queueing without network/downloads')
    mt.add_argument('--fixture', help='Path to fixture test summary for evaluation')
    mt.add_argument('--manifest', help='Path to bench2-downloads JSON manifest')
    mt.add_argument('--graph', '--db', dest='graph', help='Path to knowledge graph sqlite database')
    mt.add_argument('--tested-models', help='Path to tested_models.json file')
    mt.add_argument('--proposals', help='Path to proposals.jsonl file')
    tk = sub.add_parser('tokens', help='Report weekly token consumption across all CLIs')
    tk.add_argument('--days', type=int, default=7)
    tk.add_argument('--dry-run', action='store_true', help='Do not write tokens.jsonl')
    tk.add_argument('--prev-totals', type=Path, default=PREV_TOKENS, help='Path to previous week totals JSON')
    g = sub.add_parser('graph', help='Knowledge-graph memory for the loop')
    gsub = g.add_subparsers(dest='graph_cmd', required=True)
    gb = gsub.add_parser('build', help='Rebuild graph from scratch')
    gb.add_argument('--concepts', help='Path to concepts glossary file')
    gb.add_argument('--aliases', help='Path to aliases JSON file')
    gb.add_argument('--db', help='Path to graph SQLite database')
    gq = gsub.add_parser('query', help='Query facts from knowledge graph')
    gq.add_argument('subject', help='Subject to query')
    gq.add_argument('--relation', '-r', help='Relation to filter by')
    gq.add_argument('--aliases', help='Path to aliases JSON file')
    gq.add_argument('--db', help='Path to graph SQLite database')
    gq.add_argument('--private', action='store_true', default=False,
                    help='Include private relations (requires valid FM_HOME with AGENTS.md)')
    gq.add_argument('--related', action='store_true', default=False,
                    help='Return up to 5 possibly related items by cosine similarity')
    gq.add_argument('--hints', type=Path, default=None, help='Path to hints.jsonl')
    gq.add_argument('--tunables', type=Path, default=None, help='Path to tunables.json')
    ba = sub.add_parser('bgapps', help='Weekly background app sweep (uncovered inventory check)')
    ba.add_argument('--inventory', type=Path, default=None, help='Path to bgapps-inventory.ps1')
    ba.add_argument('--decisions', type=Path, default=None, help='Path to bgapps-decisions.md')
    ba.add_argument('--synthetic', default=None, help='Synthetic inventory output for testing')
    nm = sub.add_parser('noop-measure', help='Measure no-op wakes and token costs in transcripts')
    nm.add_argument('--transcripts', nargs='+', required=True, help='Paths to transcript JSONL files')
    nm.add_argument('--days', type=int, default=None, help='Filter wakes to the last N days')
    nm.add_argument('--json', action='store_true', help='Output JSON format')
    a = ap.parse_args(argv)
    try:
        return {'record': cmd_record, 'review': cmd_review,
                'propose': cmd_propose, 'decide': cmd_decide, 'tune': cmd_tune,
                'scan': cmd_scan,
                'models-scan': cmd_models_scan,
                'models-test': cmd_models_test,
                'tokens': cmd_tokens,
                'graph': cmd_graph,
                'noop-measure': cmd_noop_measure,
                'bgapps': cmd_bgapps}[a.cmd](a)
    except (OSError, ValueError) as exc:
        print('error: ' + json.dumps(str(exc)))
        return 1


if __name__ == '__main__':
    sys.exit(main())
