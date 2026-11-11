#!/usr/bin/env python3
"""Claude Code PreToolUse hook: deny Bash commands that send data off the machine or carry secrets.

Reads the hook JSON on stdin, splits the command into simple commands, classifies each with
leakscan.classify_command, and denies with a leak-watch reason on any finding. Never prints the
matched text. Any internal error allows the command (the hook must never wedge the session).
"""
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.join(os.path.expanduser("~"), "local-llm", "leakscan"))


def segments(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = True
    seg = []
    for tok in lex:
        if tok and set(tok) <= set(";&|()"):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def main():
    try:
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Bash":
            return 0
        command = (data.get("tool_input") or {}).get("command", "")
        import leakscan
        rules = sorted({f.rule for seg in segments(command) for f in leakscan.classify_command(seg)})
    except Exception:
        return 0
    if rules:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "leak-watch: " + ", ".join(rules)
            + " - this sends data off the machine or carries a secret; it needs the captain's explicit approval."}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
