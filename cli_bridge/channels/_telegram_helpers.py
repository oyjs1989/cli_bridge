"""Telegram channel helper utilities.

Pure functions extracted from telegram.py to reduce module size.
Re-exported from telegram.py for backward compatibility.
"""
from __future__ import annotations

import asyncio
import re

from loguru import logger

try:
    from telegram.error import NetworkError, TimedOut
except ImportError:  # pragma: no cover
    NetworkError = Exception  # type: ignore[assignment,misc]
    TimedOut = Exception  # type: ignore[assignment,misc]


def markdown_to_telegram_html(text: str) -> str:
    """Convert markdown to Telegram-safe HTML."""
    if not text:
        return ""

    # 1. Extract and protect code blocks
    code_blocks: list[str] = []

    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r"```[\w]*\n?([\s\S]*?)```", save_code_block, text)

    # 2. Extract and protect inline code
    inline_codes: list[str] = []

    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    # 3. Headers # Title -> just the title text
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)

    # 4. Blockquotes > text -> just the text (before HTML escaping)
    text = re.sub(r"^>\s*(.*)$", r"\1", text, flags=re.MULTILINE)

    # 5. Escape HTML special characters
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 6. Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 7. Bold **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 8. Italic _text_ (avoid matching inside words like some_var_name)
    text = re.sub(r"(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])", r"<i>\1</i>", text)

    # 9. Strikethrough ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    # 10. Bullet lists - item -> • item
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    # 11. Restore inline code with HTML tags
    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore code blocks with HTML tags
    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


def split_message(content: str, max_len: int = 4000) -> list[str]:
    """Split content into chunks within max_len."""
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        pos = cut.rfind("\n")
        if pos == -1:
            pos = cut.rfind(" ")
        if pos == -1:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


async def retry_async(func, *args, max_retries: int = 3, delay: float = 1.0, **kwargs):
    """Retry async function with exponential backoff on network errors."""
    last_error = None
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (NetworkError, TimedOut, ConnectionError, OSError) as e:
            if isinstance(e, NetworkError):
                err_msg = str(getattr(e, "message", "") or "")
                if err_msg.startswith("Message is not modified:"):
                    return None
            if "message is not modified" in str(e).lower():
                return None
            last_error = e
            if attempt < max_retries - 1:
                wait_time = delay * (2**attempt)
                logger.warning(
                    f"Network error (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}"
                )
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Failed after {max_retries} retries: {e}")
                raise
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return None
            if "disconnected" in str(e).lower() or "connection" in str(e).lower():
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = delay * (2**attempt)
                    logger.warning(
                        f"Connection error (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}"
                    )
                    await asyncio.sleep(wait_time)
                    continue
            raise
    raise last_error
