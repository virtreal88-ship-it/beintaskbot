"""Pure Telegram text splitting, independent of queues or credentials."""


def message_parts(text: str) -> list[str]:
    """Retain all text and the existing conservative UTF-16 size contract."""
    chunks, current, size = [], [], 0
    for char in text:
        units = 2 if ord(char) > 0xffff else 1
        if size + units > 3600:
            chunks.append(''.join(current)); current = []; size = 0
        current.append(char); size += units
    if current:
        chunks.append(''.join(current))
    return chunks if len(chunks) <= 1 else [f'({index}/{len(chunks)})\n{part}' for index, part in enumerate(chunks, 1)]
