"""No-op notification wake measurement for Claude Code master sessions.

A wake turn starts at a user message whose text is automatic, not typed by a person:
it contains `<task-notification>`, `Stop hook feedback`, or
`[SYSTEM NOTIFICATION - NOT USER INPUT]`. The turn runs until the next user message
that is not a tool_result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

NOOP_PHRASES = (
    "Captain, shipshape.",
)

WAKE_MARKERS = (
    "<task-notification>",
    "Stop hook feedback",
    "[SYSTEM NOTIFICATION - NOT USER INPUT]",
)


def extract_content_text(content: Any) -> str:
    """Extract plain text from string or structured content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                b_type = block.get("type")
                if b_type == "text":
                    parts.append(block.get("text", ""))
                elif b_type == "tool_result":
                    # Tool results can contain text or nested content
                    c = block.get("content", "")
                    if isinstance(c, str):
                        parts.append(c)
                    elif isinstance(c, list):
                        parts.append(extract_content_text(c))
        return "\n".join(parts)
    return ""


def is_wake_notification(text: str) -> bool:
    """Check if user text is an automatic wake notification."""
    return any(marker in text for marker in WAKE_MARKERS)


def is_tool_result_message(message_obj: dict) -> bool:
    """Return True if user message consists purely of tool_result blocks."""
    content = message_obj.get("content")
    if isinstance(content, list) and content:
        return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return False


def normalize_source_marker(marker: str) -> str:
    """Normalize raw marker into a short stable key."""
    if marker == "<task-notification>":
        return "task-notification"
    if marker == "Stop hook feedback":
        return "Stop hook feedback"
    if marker == "[SYSTEM NOTIFICATION - NOT USER INPUT]":
        return "SYSTEM NOTIFICATION"
    # General cleanup: strip brackets and tags
    clean = re.sub(r"[<>\[\]]", "", marker).strip()
    return clean


def extract_source_key(text: str) -> str:
    """Extract source key from wake text.

    Uses <summary> tag content if present, else normalized matching marker.
    Normalizes by stripping file paths, digit IDs, and excess whitespace.
    """
    m = re.search(r"<summary>(.*?)</summary>", text, re.DOTALL | re.IGNORECASE)
    if m:
        summary = m.group(1).strip()
        # Normalize paths, hex hashes, and digit ids
        summary = re.sub(r"/[^\s]+", "", summary)
        summary = re.sub(r"\b0x[0-9a-fA-F]+\b", "", summary)
        summary = re.sub(r"\b\d+\b", "", summary)
        summary = " ".join(summary.split())
        if summary:
            return summary

    for marker in WAKE_MARKERS:
        if marker in text:
            # Check if there is a prefix/header like Stop hook feedback or SYSTEM NOTIFICATION
            # Normalize marker
            return normalize_source_marker(marker)

    return "unknown"


def is_noop_response(text: str) -> bool:
    """Determine if assistant response is a no-op acknowledgement."""
    stripped = text.strip()
    if not stripped:
        return True
    return stripped in NOOP_PHRASES


def parse_transcript_stream(file_path: Path | str) -> Iterable[dict]:
    """Stream valid JSON objects from a JSONL transcript file."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def measure_transcripts(paths: Iterable[Path | str], days: Optional[int] = None) -> dict:
    """Measure wakes, no-op counts, and token consumption across transcripts."""
    from datetime import datetime, timezone, timedelta

    cutoff_dt = None
    if days is not None and days > 0:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)

    by_day: Dict[str, dict] = {}
    by_source: Dict[str, dict] = {}

    total_wakes = 0
    total_noop_wakes = 0
    total_cache_read = 0
    total_other_tokens = 0
    total_noop_cache_read = 0
    total_noop_other = 0

    def init_stat():
        return {
            "wakes": 0,
            "noop_wakes": 0,
            "noop_share": 0.0,
            "cache_read_tokens": 0,
            "other_tokens": 0,
            "noop_cache_read_tokens": 0,
            "noop_other_tokens": 0,
        }

    for p in paths:
        path = Path(p)
        if not path.is_file():
            continue

        in_wake = False
        current_wake_date = ""
        current_wake_source = ""
        current_cache_read = 0
        current_other_tokens = 0
        last_assistant_text = ""

        def finish_current_wake():
            nonlocal in_wake, total_wakes, total_noop_wakes
            nonlocal total_cache_read, total_other_tokens, total_noop_cache_read, total_noop_other
            if not in_wake:
                return

            is_noop = is_noop_response(last_assistant_text)
            total_wakes += 1
            if is_noop:
                total_noop_wakes += 1

            total_cache_read += current_cache_read
            total_other_tokens += current_other_tokens
            if is_noop:
                total_noop_cache_read += current_cache_read
                total_noop_other += current_other_tokens

            # Day aggregation
            if current_wake_date:
                if current_wake_date not in by_day:
                    by_day[current_wake_date] = init_stat()
                d_stat = by_day[current_wake_date]
                d_stat["wakes"] += 1
                d_stat["cache_read_tokens"] += current_cache_read
                d_stat["other_tokens"] += current_other_tokens
                if is_noop:
                    d_stat["noop_wakes"] += 1
                    d_stat["noop_cache_read_tokens"] += current_cache_read
                    d_stat["noop_other_tokens"] += current_other_tokens

            # Source aggregation
            if current_wake_source:
                if current_wake_source not in by_source:
                    by_source[current_wake_source] = init_stat()
                s_stat = by_source[current_wake_source]
                s_stat["wakes"] += 1
                s_stat["cache_read_tokens"] += current_cache_read
                s_stat["other_tokens"] += current_other_tokens
                if is_noop:
                    s_stat["noop_wakes"] += 1
                    s_stat["noop_cache_read_tokens"] += current_cache_read
                    s_stat["noop_other_tokens"] += current_other_tokens

            in_wake = False

        for record in parse_transcript_stream(path):
            if not isinstance(record, dict):
                continue

            r_type = record.get("type")
            ts_str = record.get("timestamp") or record.get("created_at") or ""

            # Check timestamp filter if days specified
            if cutoff_dt and ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if dt < cutoff_dt:
                        continue
                except Exception:
                    pass

            date_str = ts_str[:10] if len(ts_str) >= 10 else "unknown"

            msg = record.get("message")
            if not isinstance(msg, dict):
                continue

            if r_type == "user":
                if is_tool_result_message(msg):
                    # Continue current turn if inside wake
                    continue

                content_text = extract_content_text(msg.get("content"))
                if is_wake_notification(content_text):
                    # Previous wake ended, start new wake
                    finish_current_wake()
                    in_wake = True
                    current_wake_date = date_str
                    current_wake_source = extract_source_key(content_text)
                    current_cache_read = 0
                    current_other_tokens = 0
                    last_assistant_text = ""
                else:
                    # User-typed prompt ends any ongoing wake turn
                    finish_current_wake()

            elif r_type == "assistant":
                if in_wake:
                    last_assistant_text = extract_content_text(msg.get("content"))
                    usage = msg.get("usage")
                    if isinstance(usage, dict):
                        cr = usage.get("cache_read_input_tokens", 0) or 0
                        inp = usage.get("input_tokens", 0) or 0
                        cw = usage.get("cache_creation_input_tokens", 0) or 0
                        out = usage.get("output_tokens", 0) or 0
                        current_cache_read += cr
                        current_other_tokens += (inp + cw + out)

        finish_current_wake()

    # Calculate no-op shares
    for s in by_day.values():
        s["noop_share"] = (s["noop_wakes"] / s["wakes"]) if s["wakes"] > 0 else 0.0

    for s in by_source.values():
        s["noop_share"] = (s["noop_wakes"] / s["wakes"]) if s["wakes"] > 0 else 0.0

    summary = {
        "total_wakes": total_wakes,
        "total_noop_wakes": total_noop_wakes,
        "noop_share": (total_noop_wakes / total_wakes) if total_wakes > 0 else 0.0,
        "cache_read_tokens": total_cache_read,
        "other_tokens": total_other_tokens,
        "noop_cache_read_tokens": total_noop_cache_read,
        "noop_other_tokens": total_noop_other,
    }

    return {
        "summary": summary,
        "by_day": by_day,
        "by_source": by_source,
    }


def format_measurement_report(data: dict) -> str:
    """Format measurement dictionary into compact tabular report."""
    lines = []
    lines.append("## No-op notification wake measurement")
    lines.append("")
    lines.append("### Per-day breakdown")
    lines.append("| Date | Wakes | No-op wakes | No-op share | No-op cache-read | No-op other tokens |")
    lines.append("|---|---|---|---|---|---|")

    for day in sorted(data.get("by_day", {}).keys()):
        d = data["by_day"][day]
        share = f"{d['noop_share'] * 100:.1f}%"
        lines.append(
            f"| {day} | {d['wakes']} | {d['noop_wakes']} | {share} | {d['noop_cache_read_tokens']:,} | {d['noop_other_tokens']:,} |"
        )

    lines.append("")
    lines.append("### Per-source breakdown")
    lines.append("| Source key | Wakes | No-op wakes | No-op share | No-op cache-read | No-op other tokens |")
    lines.append("|---|---|---|---|---|---|")

    for src in sorted(data.get("by_source", {}).keys()):
        s = data["by_source"][src]
        share = f"{s['noop_share'] * 100:.1f}%"
        lines.append(
            f"| {src} | {s['wakes']} | {s['noop_wakes']} | {share} | {s['noop_cache_read_tokens']:,} | {s['noop_other_tokens']:,} |"
        )

    lines.append("")
    lines.append("### Total")
    lines.append("| Total wakes | Total no-op wakes | Overall no-op share | Total no-op cache-read | Total no-op other |")
    lines.append("|---|---|---|---|---|")
    sum_data = data.get("summary", {})
    tot_share = f"{sum_data.get('noop_share', 0.0) * 100:.1f}%"
    lines.append(
        f"| {sum_data.get('total_wakes', 0)} | {sum_data.get('total_noop_wakes', 0)} | {tot_share} | {sum_data.get('noop_cache_read_tokens', 0):,} | {sum_data.get('noop_other_tokens', 0):,} |"
    )

    return "\n".join(lines)
