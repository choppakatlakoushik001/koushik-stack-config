#!/usr/bin/env python3
"""Generate a losslessness manifest tying raw benchmark logs to result fields.

Read-only: never deletes, moves, or alters any file in LOGDIR.
Refuses to write output inside LOGDIR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

FILE_FIELD_MAPPING: dict[str, list[str]] = {
    "prompt.txt": ["prompt_tokens"],
    "model_reply.txt": ["completion_tokens", "model_seconds", "output_lines", "reason"],
    "patch.diff": ["changed_paths"],
    "test_output.txt": ["test_exit_code", "test_command", "verdict", "failure_kind"],
    "typecheck_output.txt": ["typecheck_exit_code", "typecheck_enabled", "checker"],
    "spec.json": ["spec_sha256", "base_commit", "repo", "test_command", "token_estimate"],
    "stdout.txt": ["wall_seconds"],
    "stderr.txt": ["wall_seconds"],
}


def hash_file(path: Path) -> str:
    """Return SHA-256 hex digest of file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def find_associated_verdict(file_path: Path, logdir_root: Path) -> dict[str, object] | None:
    """Find and load verdict.json or summary.json in the file's dir or parent up to logdir."""
    curr = file_path.parent
    while True:
        for candidate_name in ("verdict.json", "summary.json"):
            vpath = curr / candidate_name
            if vpath.is_file():
                try:
                    data = json.loads(vpath.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
        if curr == logdir_root or curr.parent == curr:
            break
        curr = curr.parent
    return None


def determine_fields(file_path: Path, verdict_data: dict[str, object] | None) -> list[str]:
    """Determine result fields the raw file maps to, when named in verdict_data."""
    if file_path.name in ("verdict.json", "summary.json"):
        if verdict_data and isinstance(verdict_data, dict):
            return sorted(str(k) for k in verdict_data.keys())
        return []

    candidates = FILE_FIELD_MAPPING.get(file_path.name, [])
    if not candidates or not verdict_data:
        return []

    return [f for f in candidates if f in verdict_data]


def build_manifest(logdir: Path) -> dict[str, object]:
    """Scan logdir in read-only mode and build manifest records."""
    logdir = logdir.resolve()
    if not logdir.is_dir():
        raise ValueError(f"LOGDIR does not exist or is not a directory: {logdir}")

    records: list[dict[str, object]] = []
    # Collect all regular files in sorted order
    files = sorted([p for p in logdir.rglob("*") if p.is_file()])

    for fpath in files:
        rel = fpath.relative_to(logdir).as_posix()
        size = fpath.stat().st_size
        sha = hash_file(fpath)
        verdict_data = find_associated_verdict(fpath, logdir)
        fields = determine_fields(fpath, verdict_data)

        records.append({
            "path": rel,
            "size": size,
            "sha256": sha,
            "fields": fields,
        })

    return {
        "manifest_version": 1,
        "logdir": str(logdir),
        "file_count": len(records),
        "total_bytes": sum(int(r["size"]) for r in records),
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logdir", type=Path, help="Directory containing benchmark logs to manifest")
    ap.add_argument("--out", type=Path, required=True, help="Destination path for manifest JSON")
    args = ap.parse_args(argv)

    logdir = args.logdir.resolve()
    if not logdir.is_dir():
        print(f"Error: LOGDIR does not exist or is not a directory: {logdir}", file=sys.stderr)
        return 1

    out = args.out.resolve()
    if out == logdir or logdir in out.parents:
        print(f"Error: Refusing to write manifest inside LOGDIR: {out}", file=sys.stderr)
        return 1

    try:
        manifest = build_manifest(logdir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote manifest with {manifest['file_count']} records to {out}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
