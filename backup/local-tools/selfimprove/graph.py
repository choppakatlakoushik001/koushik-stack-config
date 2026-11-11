"""Knowledge-graph memory for the self-improvement loop.

Local SQLite table of facts (thing, relation, thing, date, source) fed from
records, proposals, decisions, model tests and tokens; plus concepts table
tracking technical terms taught from a glossary file.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
GRAPH_DB_DEFAULT = ROOT / 'graph.sqlite'
ALIASES_PATH_DEFAULT = ROOT / 'aliases.json'
CONCEPTS_DEFAULT = Path('/home/chopp/kunchen/crew/kunchen_firstmate/data/concepts.md')
DEFAULT_EMBEDDING_MODEL_NAME = 'bge-small-en-v1.5'
DEFAULT_EMBEDDING_MODEL_PATH = Path.home() / 'local-llm' / 'models' / 'bge-small-en-v1.5' / 'bge-small-en-v1.5-q8_0.gguf'
DEFAULT_LLAMA_SERVER_BIN = Path.home() / 'local-llm' / 'llama.cpp' / 'src' / 'build' / 'bin' / 'llama-server'

# Public relations accessible without --private / FM_HOME authorization
PUBLIC_RELATIONS: Set[str] = {
    'declined',
    'status',
    'verdict',
    'tested',
    'publisher',
    'size_gb',
    'fits_threshold',
    'type',
    'best_candidate',
}


def is_private_allowed() -> bool:
    """Return True if FM_HOME is set to an existing directory containing AGENTS.md."""
    fm_home = os.environ.get('FM_HOME')
    if not fm_home:
        return False
    p = Path(fm_home)
    return p.is_dir() and (p / 'AGENTS.md').is_file()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    subject TEXT NOT NULL,
    relation TEXT NOT NULL,
    object TEXT NOT NULL,
    date TEXT,
    source TEXT NOT NULL,
    display TEXT,
    UNIQUE(subject, relation, object, date, source)
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_relation ON facts(relation);

CREATE TABLE IF NOT EXISTS concepts (
    term TEXT PRIMARY KEY,
    first_taught TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    text_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding TEXT NOT NULL,
    dim INTEGER,
    text TEXT,
    created_at TEXT,
    PRIMARY KEY(text_hash, model)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);
"""

# Fixed verdict vocabulary mapping raw verdicts to canonical classes
VERDICT_VOCABULARY = {
    'fallback-only': 'tried',
    'tried': 'tried',
    'declined': 'declined',
    'no': 'declined',
    'rejected': 'declined',
    'replace': 'replace',
    'keep': 'keep',
    'clean up': 'clean up',
    'clean-up': 'clean up',
    'pass': 'pass',
    'fail': 'fail',
    'pending': 'pending',
}


def normalize_name(text: Optional[str]) -> str:
    """Normalize subject/object entity name: lowercase, strip, collapse internal whitespace."""
    if not text:
        return ''
    return ' '.join(str(text).strip().split()).lower()


def load_aliases(alias_path: Path | str = None) -> Dict[str, str]:
    """Load alias table mapping raw/alias string to canonical name."""
    p = Path(alias_path) if alias_path else ALIASES_PATH_DEFAULT
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        if isinstance(data, dict):
            # Normalize keys and values
            res = {}
            for k, v in data.items():
                if k and v:
                    res[normalize_name(k)] = normalize_name(v)
            return res
    except Exception:
        pass
    return {}


def canonicalize_name(text: Optional[str], alias_map: Optional[Dict[str, str]] = None) -> str:
    """Normalize and resolve through alias map."""
    norm = normalize_name(text)
    if not norm:
        return ''
    if alias_map and norm in alias_map:
        return alias_map[norm]
    return norm


def canonicalize_verdict(raw_verdict: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Map raw verdict to (canonical_verdict, unknown_verdict_warning).

    Returns (canonical_verdict, None) if known, or (norm_verdict, warning_str) if unknown.
    """
    if not raw_verdict:
        return None, None
    norm = normalize_name(raw_verdict)
    if norm in VERDICT_VOCABULARY:
        return VERDICT_VOCABULARY[norm], None
    return norm, f"unknown verdict word: '{raw_verdict}'"



def normalize_date(val: Any) -> Optional[str]:
    """Normalize date representations (timestamps, ISO strings, YYYY-MM-DD) to YYYY-MM-DD."""
    if not val:
        return None
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, timezone.utc).strftime('%Y-%m-%d')
    if isinstance(val, str):
        val = val.strip()
        if len(val) >= 10 and val[4] == '-' and val[7] == '-':
            return val[:10]
        try:
            dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d')
        except Exception:
            return val
    return str(val)


def model_subject_aliases(name_or_repo: str) -> List[str]:
    """Extract canonical subject names/aliases for a model repo or name."""
    if not name_or_repo or not isinstance(name_or_repo, str):
        return []
    s = name_or_repo.strip()
    results = [s]
    short = s.split('/')[-1]
    if short != s:
        results.append(short)
    for suffix in ('-GGUF', '.gguf', '-gguf', '.bin'):
        if short.endswith(suffix):
            results.append(short[:-len(suffix)])
        if s.endswith(suffix):
            results.append(s[:-len(suffix)])
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for item in results:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def init_db(db_path: Path | str = None, recreate: bool = False) -> sqlite3.Connection:
    """Initialize SQLite database with facts and concepts tables."""
    p = Path(db_path) if db_path else GRAPH_DB_DEFAULT
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    with conn:
        if recreate:
            conn.execute("DROP TABLE IF EXISTS facts")
            conn.execute("DROP TABLE IF EXISTS concepts")
            conn.execute("DROP INDEX IF EXISTS idx_facts_subject")
            conn.execute("DROP INDEX IF EXISTS idx_facts_relation")
        conn.executescript(SCHEMA_SQL)
    return conn


def parse_concepts_glossary(concepts_path: Path | str) -> Dict[str, str]:
    """Parse technical terms and dates from a glossary file with `## Term (date)` headings."""
    p = Path(concepts_path)
    if not p.exists():
        return {}
    text = p.read_text(encoding='utf-8', errors='ignore')
    pattern = re.compile(r'^##\s+(.+?)\s*\((\d{4}-\d{2}-\d{2})\)\s*$', re.M)
    concepts: Dict[str, str] = {}
    for match in pattern.finditer(text):
        term = match.group(1).strip()
        date_str = match.group(2).strip()
        if term not in concepts or date_str < concepts[term]:
            concepts[term] = date_str
    return concepts


def extract_facts_records(records_path: Path | str, alias_map: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, str, Optional[str], str, Optional[str]]]:
    """Extract facts from records.jsonl."""
    p = Path(records_path)
    if not p.exists():
        return []
    facts = []
    source = 'records.jsonl'
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        raw_task = r.get('task')
        if not raw_task:
            continue
        raw_task_str = str(raw_task)
        canon_task = canonicalize_name(raw_task_str, alias_map)
        date = normalize_date(r.get('at'))
        if r.get('outcome'):
            facts.append((canon_task, 'outcome', normalize_name(r['outcome']), date, source, raw_task_str))
        if r.get('labour'):
            raw_lab = str(r['labour'])
            facts.append((canon_task, 'labour', canonicalize_name(raw_lab, alias_map), date, source, raw_task_str))
        if r.get('master'):
            raw_mas = str(r['master'])
            facts.append((canon_task, 'master', canonicalize_name(raw_mas, alias_map), date, source, raw_task_str))
        if r.get('tool'):
            raw_tool = str(r['tool'])
            facts.append((canon_task, 'tool', canonicalize_name(raw_tool, alias_map), date, source, raw_task_str))
        if r.get('minutes') is not None:
            facts.append((canon_task, 'duration_minutes', str(r['minutes']), date, source, raw_task_str))
        for b in r.get('blockers') or []:
            if b:
                raw_b = str(b)
                canon_b = canonicalize_name(raw_b, alias_map)
                facts.append((canon_task, 'blocked_by', canon_b, date, source, raw_task_str))
                facts.append((canon_b, 'blocked_task', canon_task, date, source, raw_b))
        tuning = r.get('tuning')
        if isinstance(tuning, dict):
            if tuning.get('name'):
                facts.append((canon_task, 'tuning_name', str(tuning['name']), date, source, raw_task_str))
            if tuning.get('trial'):
                facts.append((canon_task, 'tuning_trial', str(tuning['trial']), date, source, raw_task_str))
    return facts


def extract_facts_proposals(proposals_path: Path | str, alias_map: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, str, Optional[str], str, Optional[str]]]:
    """Extract facts from proposals.jsonl (proposals and their decision events)."""
    p = Path(proposals_path)
    if not p.exists():
        return []
    facts = []
    source = 'proposals.jsonl'

    raw = p.read_text(encoding='utf-8', errors='ignore')
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    proposals: List[Dict[str, Any]] = []
    decisions: Dict[int, Dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'decision':
            idx = event.get('proposal')
            if isinstance(idx, int):
                decisions[idx] = event
        else:
            proposals.append(event)

    for idx, prop in enumerate(proposals, 1):
        prop_id = f"proposal:{idx}"
        date = normalize_date(prop.get('at'))
        if prop.get('idea'):
            facts.append((prop_id, 'idea', str(prop['idea']), date, source, prop_id))
        if prop.get('goal'):
            facts.append((prop_id, 'goal', str(prop['goal']), date, source, prop_id))
        if prop.get('tool'):
            tool_name = str(prop['tool'])
            canon_tool = canonicalize_name(tool_name, alias_map)
            facts.append((prop_id, 'tool', canon_tool, date, source, prop_id))
            facts.append((canon_tool, 'proposed_in', prop_id, date, source, tool_name))
        if prop.get('source'):
            facts.append((prop_id, 'source', str(prop['source']), date, source, prop_id))
        facts.append((prop_id, 'status', str(prop.get('status', 'open')), date, source, prop_id))

        if idx in decisions:
            dec = decisions[idx]
            dec_date = normalize_date(dec.get('at')) or date
            dec_val = str(dec.get('decision', ''))
            facts.append((prop_id, 'decision', dec_val, dec_date, source, prop_id))
            facts.append((prop_id, 'status', dec_val, dec_date, source, prop_id))
            if dec.get('note'):
                facts.append((prop_id, 'decision_note', str(dec['note']), dec_date, source, prop_id))
            is_declined = (dec_val == 'rejected') or (dec.get('note') and 'declined' in str(dec['note']).lower())
            if is_declined:
                facts.append((prop_id, 'declined', 'true', dec_date, source, prop_id))
                # If tool or idea mentioned a model/tool
                candidates = []
                if prop.get('tool'):
                    candidates.append(str(prop['tool']))
                # Check for model-like tokens in idea
                for tok in re.findall(r'[A-Za-z0-9_\.-]+', prop.get('idea', '')):
                    if any(prefix in tok.lower() for prefix in ('qwen', 'llama', 'deepseek', 'gemma', 'mistral')):
                        candidates.append(tok)
                for cand in candidates:
                    for alias in model_subject_aliases(cand):
                        canon_alias = canonicalize_name(alias, alias_map)
                        facts.append((canon_alias, 'declined', 'true', dec_date, source, alias))
                        facts.append((canon_alias, 'verdict', 'declined', dec_date, source, alias))
                        facts.append((canon_alias, 'status', 'declined', dec_date, source, alias))

    return facts


def extract_facts_tested_models(tested_models_path: Path | str, alias_map: Optional[Dict[str, str]] = None) -> Tuple[List[Tuple[str, str, str, Optional[str], str, Optional[str]]], List[str]]:
    """Extract facts from tested_models.json and report any unknown verdict warnings."""
    p = Path(tested_models_path)
    if not p.exists():
        return [], []
    try:
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
    except Exception:
        return [], []
    if not isinstance(data, dict):
        return [], []
    facts = []
    warnings = []
    source = 'tested_models.json'
    for repo, meta in data.items():
        if not isinstance(meta, dict):
            continue
        date = normalize_date(meta.get('tested_at'))
        verdict = meta.get('verdict')
        summary_file = meta.get('summary_file')
        aliases = model_subject_aliases(repo)
        for subj in aliases:
            canon_subj = canonicalize_name(subj, alias_map)
            facts.append((canon_subj, 'tested', 'true', date, source, subj))
            if verdict:
                raw_v = str(verdict)
                canon_v, warn = canonicalize_verdict(raw_v)
                if warn:
                    warnings.append(f"{subj}: {warn}")
                facts.append((canon_subj, 'verdict', canon_v, date, source, subj))
                facts.append((canon_subj, 'status', canon_v, date, source, subj))
                if canon_v in ('declined', 'tried'):
                    facts.append((canon_subj, 'declined', 'true', date, source, subj))
            if summary_file:
                facts.append((canon_subj, 'summary_file', str(summary_file), date, source, subj))
            if meta.get('type'):
                facts.append((canon_subj, 'type', str(meta['type']), date, source, subj))
    return facts, warnings


def extract_facts_advancements(advancements_path: Path | str, alias_map: Optional[Dict[str, str]] = None) -> Tuple[List[Tuple[str, str, str, Optional[str], str, Optional[str]]], List[str]]:
    """Extract facts from advancements.jsonl and report any unknown verdict warnings."""
    p = Path(advancements_path)
    if not p.exists():
        return [], []
    facts = []
    warnings = []
    source = 'advancements.jsonl'
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        date = normalize_date(entry.get('at'))
        if entry.get('type') == 'model_candidate' or 'repo' in entry:
            repo = entry.get('repo', '')
            aliases = model_subject_aliases(repo)
            for subj in aliases:
                canon_subj = canonicalize_name(subj, alias_map)
                facts.append((canon_subj, 'type', 'model_candidate', date, source, subj))
                if entry.get('publisher'):
                    facts.append((canon_subj, 'publisher', str(entry['publisher']), date, source, subj))
                if entry.get('size_gb') is not None:
                    facts.append((canon_subj, 'size_gb', str(entry['size_gb']), date, source, subj))
                if entry.get('fits') is not None:
                    facts.append((canon_subj, 'fits_threshold', str(entry['fits']), date, source, subj))
                if entry.get('verdict'):
                    raw_v = str(entry['verdict'])
                    canon_v, warn = canonicalize_verdict(raw_v)
                    if warn:
                        warnings.append(f"{subj}: {warn}")
                    facts.append((canon_subj, 'verdict', canon_v, date, source, subj))
                    facts.append((canon_subj, 'status', canon_v, date, source, subj))
                    if canon_v in ('declined', 'tried'):
                        facts.append((canon_subj, 'declined', 'true', date, source, subj))
                if entry.get('best_candidate'):
                    facts.append((canon_subj, 'best_candidate', 'true', date, source, subj))
        elif entry.get('item'):
            tool = str(entry.get('tool', 'tool'))
            canon_tool = canonicalize_name(tool, alias_map)
            item = str(entry['item'])
            facts.append((canon_tool, 'upstream_advancement', item, date, source, tool))
            if entry.get('proposal'):
                facts.append((canon_tool, 'advancement_proposal', f"proposal:{entry['proposal']}", date, source, tool))
    return facts, warnings


def extract_facts_tokens(tokens_path: Path | str, alias_map: Optional[Dict[str, str]] = None) -> List[Tuple[str, str, str, Optional[str], str, Optional[str]]]:
    """Extract facts from tokens.jsonl."""
    p = Path(tokens_path)
    if not p.exists():
        return []
    facts = []
    source = 'tokens.jsonl'
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        cli = rec.get('cli')
        if not cli:
            continue
        raw_cli = str(cli)
        canon_cli = canonicalize_name(raw_cli, alias_map)
        date = normalize_date(rec.get('timestamp') or rec.get('at'))
        if rec.get('total') is not None:
            facts.append((canon_cli, 'tokens_recorded', str(rec['total']), date, source, raw_cli))
        if rec.get('model'):
            raw_m = str(rec['model'])
            canon_m = canonicalize_name(raw_m, alias_map)
            facts.append((canon_cli, 'used_model', canon_m, date, source, raw_cli))
            facts.append((canon_m, 'used_by', canon_cli, date, source, raw_m))
    return facts



def build_graph(
    db_path: Path | str = None,
    concepts_path: Path | str = None,
    records_path: Path | str = None,
    proposals_path: Path | str = None,
    advancements_path: Path | str = None,
    tested_models_path: Path | str = None,
    tokens_path: Path | str = None,
    aliases_path: Path | str = None,
) -> Dict[str, Any]:
    """Rebuild knowledge graph from scratch.
    Plain code only, idempotent and fast.
    """
    p_db = Path(db_path) if db_path else GRAPH_DB_DEFAULT
    conn = init_db(p_db, recreate=True)

    # Defaults if paths not explicitly given (pass False or empty Path to disable a source)
    p_rec = records_path if records_path is not None else (ROOT / 'records.jsonl')
    p_prop = proposals_path if proposals_path is not None else (ROOT / 'proposals.jsonl')
    p_adv = advancements_path if advancements_path is not None else (ROOT / 'advancements.jsonl')
    p_test = tested_models_path if tested_models_path is not None else (Path.home() / 'local-llm' / 'bench-logs' / 'local-model-ab-test' / 'tested_models.json')
    p_tok = tokens_path if tokens_path is not None else (ROOT / 'tokens.jsonl')

    alias_map = load_aliases(aliases_path)
    all_facts: Set[Tuple[str, str, str, Optional[str], str, Optional[str]]] = set()
    warnings: List[str] = []

    if p_rec:
        for f in extract_facts_records(p_rec, alias_map=alias_map):
            all_facts.add(f)
    if p_prop:
        for f in extract_facts_proposals(p_prop, alias_map=alias_map):
            all_facts.add(f)
    if p_adv:
        adv_facts, adv_warns = extract_facts_advancements(p_adv, alias_map=alias_map)
        for f in adv_facts:
            all_facts.add(f)
        warnings.extend(adv_warns)

    if p_test:
        test_facts, test_warns = extract_facts_tested_models(p_test, alias_map=alias_map)
        for f in test_facts:
            all_facts.add(f)
        warnings.extend(test_warns)

    if p_tok:
        for f in extract_facts_tokens(p_tok, alias_map=alias_map):
            all_facts.add(f)

    # Concepts file: no default inside Firstmate; only parsed if provided and exists
    concepts_dict: Dict[str, str] = {}
    if concepts_path:
        concepts_dict = parse_concepts_glossary(concepts_path)
        for term, dt in concepts_dict.items():
            canon_term = canonicalize_name(term, alias_map)
            all_facts.add((canon_term, 'taught_on', dt, dt, 'concepts', term))
            all_facts.add((canon_term, 'status', 'taught', dt, 'concepts', term))

    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO facts (subject, relation, object, date, source, display) VALUES (?, ?, ?, ?, ?, ?)",
            list(all_facts)
        )
        if concepts_dict:
            conn.executemany(
                "INSERT OR REPLACE INTO concepts (term, first_taught) VALUES (?, ?)",
                list(concepts_dict.items())
            )

    conn.close()

    for w in warnings:
        print(f"graph build warning: {w}")

    return {'facts': len(all_facts), 'concepts': len(concepts_dict), 'warnings': warnings}


def query_graph(
    subject: str,
    relation: Optional[str] = None,
    db_path: Path | str = None,
    aliases_path: Path | str = None,
    private: bool = False,
) -> List[Tuple[str, str, str, Optional[str], str, Optional[str]]]:
    """Query facts matching subject (and optional relation), case-insensitively and alias-resolved."""
    if private:
        if not is_private_allowed():
            raise PermissionError("Private query refused: FM_HOME must be set to a directory containing AGENTS.md.")
    elif relation:
        norm_rel = normalize_name(relation)
        if norm_rel not in PUBLIC_RELATIONS:
            raise PermissionError(f"Relation '{relation}' is private: rerun with --private (requires valid FM_HOME).")

    p = Path(db_path) if db_path else GRAPH_DB_DEFAULT
    if not p.exists():
        return []
    alias_map = load_aliases(aliases_path)
    canon_subject = canonicalize_name(subject, alias_map)

    conn = sqlite3.connect(p)
    cur = conn.cursor()
    if relation:
        norm_rel = normalize_name(relation)
        cur.execute(
            """
            SELECT subject, relation, object, date, source, display
            FROM facts
            WHERE subject = ? COLLATE NOCASE
              AND relation = ? COLLATE NOCASE
            ORDER BY date ASC, relation ASC, object ASC
            """,
            (canon_subject, norm_rel)
        )
    else:
        cur.execute(
            """
            SELECT subject, relation, object, date, source, display
            FROM facts
            WHERE subject = ? COLLATE NOCASE
            ORDER BY date ASC, relation ASC, object ASC
            """,
            (canon_subject,)
        )
    rows = cur.fetchall()
    conn.close()

    if not private:
        rows = [r for r in rows if normalize_name(r[1]) in PUBLIC_RELATIONS]

    return rows


def format_fact_line(fact: Tuple[Any, ...]) -> str:
    """Format one fact compactly for single-line terminal printing."""
    subj = fact[0]
    rel = fact[1]
    obj = fact[2]
    date = fact[3]
    source = fact[4]
    display = fact[5] if len(fact) > 5 and fact[5] else subj
    date_str = f"{date}, " if date else ""
    return f"{display} {rel} {obj} ({date_str}{source})"


def get_declined_models(db_path: Path | str = None) -> Set[str]:
    """Return set of model repos or names known to be declined or tried in the knowledge graph."""
    p = Path(db_path) if db_path else GRAPH_DB_DEFAULT
    if not p.exists():
        return set()
    try:
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT subject FROM facts
            WHERE relation = 'declined'
               OR object IN ('declined', 'tried')
               OR (relation = 'verdict' AND object IN ('declined', 'tried', 'no', 'rejected', 'fallback-only'))
               OR (relation = 'status' AND object IN ('declined', 'tried', 'rejected', 'fallback-only'))
            """
        )
        declined = {row[0] for row in cur.fetchall()}
        conn.close()
        return declined
    except Exception:
        return set()


def is_candidate_declined(candidate: Dict[str, Any], declined_set: Set[str], aliases_path: Path | str = None) -> bool:
    """Check if candidate model matches any subject in declined_set."""
    if not candidate or not declined_set:
        return False
    alias_map = load_aliases(aliases_path)
    keys_to_check = set()
    for field in ('repo', 'name', 'canonical_id'):
        val = candidate.get(field)
        if val:
            for alias in model_subject_aliases(val):
                keys_to_check.add(alias)
                keys_to_check.add(canonicalize_name(alias, alias_map))
    return any(k in declined_set for k in keys_to_check)



def graph_size_summary(db_path: Path | str = None) -> str:
    """Return one-line summary of knowledge graph size."""
    p = Path(db_path) if db_path else GRAPH_DB_DEFAULT
    if not p.exists():
        return "Knowledge graph: not built yet."
    try:
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM facts")
        n_facts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM concepts")
        n_concepts = cur.fetchone()[0]
        conn.close()
        return f"Knowledge graph: {n_facts} facts, {n_concepts} concepts."
    except Exception as e:
        return f"Knowledge graph: error ({e})."


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Compute cosine similarity between two vectors: dot(a,b) / (|a| * |b|)."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a_sq += a * a
        norm_b_sq += b * b
    if norm_a_sq <= 0.0 or norm_b_sq <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a_sq) * math.sqrt(norm_b_sq))


def hash_text(text: str) -> str:
    """Deterministic sha256 hash of normalized text."""
    return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()


def get_cached_embedding(db_path: Path | str, text: str, model_name: str) -> Optional[List[float]]:
    """Retrieve cached embedding for text and model_name from SQLite, if present."""
    p = Path(db_path) if db_path else GRAPH_DB_DEFAULT
    if not p.exists():
        return None
    try:
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute(
            "SELECT embedding FROM embeddings WHERE text_hash = ? AND model = ?",
            (hash_text(text), model_name)
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def save_cached_embedding(
    db_path: Path | str,
    text: str,
    model_name: str,
    embedding: List[float],
    created_at: Optional[str] = None
) -> None:
    """Save embedding vector to SQLite embeddings table."""
    p = Path(db_path) if db_path else GRAPH_DB_DEFAULT
    if not p.exists():
        init_db(p)
    now_str = created_at or datetime.now(timezone.utc).isoformat()
    thash = hash_text(text)
    conn = sqlite3.connect(p)
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO embeddings (text_hash, model, embedding, dim, text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (thash, model_name, json.dumps(embedding), len(embedding), text, now_str)
        )
    conn.close()


class FakeEmbedder:
    """Fake embedder for unit tests, returning deterministic synthetic vectors."""
    def __init__(
        self,
        vectors: Optional[Dict[str, List[float]]] = None,
        dim: int = 16,
        fail: bool = False
    ):
        self.vectors = vectors or {}
        self.dim = dim
        self.fail = fail
        self.call_count = 0

    def embed(self, text: str) -> List[float]:
        self.call_count += 1
        if self.fail:
            raise RuntimeError("Simulated embedder failure")
        if text in self.vectors:
            return self.vectors[text]
        # Return deterministic vector based on text hash
        h = hashlib.sha256(text.encode('utf-8')).digest()
        vec = [float(b) / 255.0 for b in h[:self.dim]]
        norm = math.sqrt(sum(x*x for x in vec))
        return [x / norm for x in vec] if norm > 0 else vec

    def __enter__(self):
        if self.fail:
            raise RuntimeError("Simulated embedder server failure")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class LlamaServerEmbedder:
    """Embedder managing local llama-server --embedding on demand."""
    def __init__(
        self,
        model_path: Path | str = None,
        server_bin: Path | str = None,
        port: Optional[int] = None,
        host: str = '127.0.0.1',
        timeout: float = 8.0,
    ):
        self.model_path = Path(model_path) if model_path else DEFAULT_EMBEDDING_MODEL_PATH
        self.server_bin = Path(server_bin) if server_bin else DEFAULT_LLAMA_SERVER_BIN
        self.host = host
        self.port = port
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen] = None
        self._actual_port: Optional[int] = None

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((self.host, 0))
            return s.getsockname()[1]

    def __enter__(self):
        if not self.model_path.exists():
            raise FileNotFoundError(f"Embedding model not found: {self.model_path}")
        if not self.server_bin.exists():
            raise FileNotFoundError(f"llama-server binary not found: {self.server_bin}")

        self._actual_port = self.port or self._find_free_port()
        cmd = [
            str(self.server_bin),
            '-m', str(self.model_path),
            '--embedding',
            '--port', str(self._actual_port),
            '--host', self.host,
            '-c', '512',
            '-t', '2',
            '--log-disable',
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://{self.host}:{self._actual_port}/health"
        start_time = time.time()
        ready = False
        while time.time() - start_time < self.timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited prematurely with code {self.proc.returncode}")
            try:
                req = urllib.request.Request(url)
                with opener.open(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.05)

        if not ready:
            self.__exit__(None, None, None)
            raise TimeoutError(f"llama-server failed to become ready within {self.timeout}s")

        return self

    def embed(self, text: str) -> List[float]:
        if not self.proc or self._actual_port is None:
            raise RuntimeError("Embedder server not running (use as context manager)")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://{self.host}:{self._actual_port}/embedding"
        req = urllib.request.Request(
            url,
            data=json.dumps({'content': text}).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
        )
        with opener.open(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, list) and len(data) > 0:
                emb = data[0].get('embedding')
            elif isinstance(data, dict):
                emb = data.get('embedding')
            else:
                raise ValueError(f"Unexpected response format from embedding server: {type(data)}")
            if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                emb = emb[0]
            return emb

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=1.0)
                except Exception:
                    pass
            self.proc = None


def get_graph_search_candidates(db_path: Path | str = None, private: bool = False) -> List[str]:
    """Return unique candidate entity names from the knowledge graph.
    Enforces public-only lock unless private is True and is_private_allowed() is True.
    """
    if private:
        if not is_private_allowed():
            raise PermissionError("Private query refused: FM_HOME must be set to a directory containing AGENTS.md.")
    p = Path(db_path) if db_path else GRAPH_DB_DEFAULT
    if not p.exists():
        return []

    conn = sqlite3.connect(p)
    cur = conn.cursor()
    candidates = set()

    if private:
        cur.execute("SELECT DISTINCT subject FROM facts")
        for row in cur.fetchall():
            if row[0]:
                candidates.add(row[0])
        cur.execute("SELECT DISTINCT display FROM facts WHERE display IS NOT NULL AND display != ''")
        for row in cur.fetchall():
            if row[0]:
                candidates.add(row[0])
        cur.execute("SELECT DISTINCT term FROM concepts")
        for row in cur.fetchall():
            if row[0]:
                candidates.add(row[0])
    else:
        placeholders = ','.join('?' for _ in PUBLIC_RELATIONS)
        cur.execute(f"SELECT DISTINCT subject FROM facts WHERE LOWER(relation) IN ({placeholders})", list(PUBLIC_RELATIONS))
        for row in cur.fetchall():
            if row[0]:
                candidates.add(row[0])
        cur.execute(f"SELECT DISTINCT display FROM facts WHERE LOWER(relation) IN ({placeholders}) AND display IS NOT NULL AND display != ''", list(PUBLIC_RELATIONS))
        for row in cur.fetchall():
            if row[0]:
                candidates.add(row[0])
        cur.execute("SELECT DISTINCT term FROM concepts")
        for row in cur.fetchall():
            if row[0]:
                candidates.add(row[0])

    conn.close()
    return sorted(candidates)


def find_related_items(
    query_text: str,
    candidate_texts: List[str],
    embedder: Any,
    db_path: Path | str = None,
    model_name: str = DEFAULT_EMBEDDING_MODEL_NAME,
    threshold: float = 0.65,
    top_k: int = 5,
    exact_match_filter: Optional[str] = None
) -> List[Tuple[str, float]]:
    """Find up to top_k candidate texts with cosine similarity >= threshold to query_text.
    Uses cached embeddings in graph.sqlite whenever available.
    """
    if not query_text or not candidate_texts:
        return []

    norm_query = normalize_name(query_text)
    exact_filter_norm = normalize_name(exact_match_filter) if exact_match_filter else norm_query

    # Filter out candidates matching the exact query filter
    filtered_candidates: List[str] = []
    seen_norm: Set[str] = set()
    for c in candidate_texts:
        cnorm = normalize_name(c)
        if cnorm and cnorm != exact_filter_norm and cnorm not in seen_norm:
            seen_norm.add(cnorm)
            filtered_candidates.append(c)

    if not filtered_candidates:
        return []

    p_db = Path(db_path) if db_path else GRAPH_DB_DEFAULT

    # Get query embedding (cache or compute)
    q_emb = get_cached_embedding(p_db, query_text, model_name)
    if q_emb is None:
        q_emb = embedder.embed(query_text)
        save_cached_embedding(p_db, query_text, model_name, q_emb)

    results: List[Tuple[str, float]] = []
    for cand in filtered_candidates:
        c_emb = get_cached_embedding(p_db, cand, model_name)
        if c_emb is None:
            c_emb = embedder.embed(cand)
            save_cached_embedding(p_db, cand, model_name, c_emb)
        sim = cosine_similarity(q_emb, c_emb)
        if sim >= threshold:
            results.append((cand, sim))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]
