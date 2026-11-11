"""Synthetic test fixtures for no-op notification wake measurement."""

SYNTHETIC_WAKE_SESSION = """{"type": "user", "timestamp": "2026-11-01T10:00:00Z", "message": {"content": "Hello assistant, write a quick test."}}
{"type": "assistant", "timestamp": "2026-11-01T10:00:05Z", "message": {"content": "Sure, here is a test.", "usage": {"input_tokens": 100, "cache_read_input_tokens": 500, "cache_creation_input_tokens": 50, "output_tokens": 20}}}
{"type": "user", "timestamp": "2026-11-01T10:05:00Z", "message": {"content": "<task-notification>\\n<summary>background build finished</summary>\\nJob 42 exited 0\\n</task-notification>"}}
{"type": "assistant", "timestamp": "2026-11-01T10:05:10Z", "message": {"content": [{"type": "text", "text": "Captain, shipshape.\\n"}], "usage": {"input_tokens": 150, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0, "output_tokens": 10}}}
{"type": "user", "timestamp": "2026-11-01T10:10:00Z", "message": {"content": "Stop hook feedback: test warning"}}
{"type": "assistant", "timestamp": "2026-11-01T10:10:15Z", "message": {"content": "Investigating warning...", "usage": {"input_tokens": 200, "cache_read_input_tokens": 1200, "cache_creation_input_tokens": 0, "output_tokens": 30}}}
{"type": "user", "timestamp": "2026-11-01T10:10:20Z", "message": {"content": [{"type": "tool_result", "content": "file contents"}]}}
{"type": "assistant", "timestamp": "2026-11-01T10:10:30Z", "message": {"content": "Resolved warning.", "usage": {"input_tokens": 250, "cache_read_input_tokens": 1400, "cache_creation_input_tokens": 0, "output_tokens": 40}}}
{"type": "user", "timestamp": "2026-11-01T10:15:00Z", "message": {"content": "[SYSTEM NOTIFICATION - NOT USER INPUT] Watcher alert on port 8080"}}
{"type": "assistant", "timestamp": "2026-11-01T10:15:05Z", "message": {"content": "", "usage": {"input_tokens": 100, "cache_read_input_tokens": 800, "cache_creation_input_tokens": 0, "output_tokens": 5}}}
"""
