"""Деление длинных ответов бота на сообщения (лимит Telegram — 4096 символов)."""

MAX_MESSAGE_LEN = 4000


def chunk_lines(lines: list[str], limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Склеить строки в сообщения не длиннее limit (строки не рвём, слишком длинную — обрезаем)."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        line = line[:limit]
        if current and len(current) + 1 + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def pack_blocks(blocks: list[list[str]], limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Блоки строк (разделы отчёта) -> сообщения: разделы идут подряд через пустую строку, пока влезают,
    раздел не разрывается, если помещается в одно сообщение; слишком длинный режется по строкам."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        text = "\n".join(block)
        if len(text) > limit:
            if current:
                messages.append(current)
                current = ""
            messages.extend(chunk_lines(block, limit))
        elif current and len(current) + 2 + len(text) > limit:
            messages.append(current)
            current = text
        else:
            current = f"{current}\n\n{text}" if current else text
    if current:
        messages.append(current)
    return messages
