"""Read the model's one-line explanation."""


def reason_line(reply: str) -> str:
    """Return the explanation after the first case-insensitive REASON: line."""
    for line in reply.splitlines():
        if line.strip().upper().startswith('REASON:'):
            return line.split(':', 1)[1].strip()
    return ''
