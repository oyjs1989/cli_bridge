"""Shared ACP protocol utilities used by both StdioACPAdapter and ACPAdapter.

Pure stateless functions — no side effects, no I/O.
"""
from __future__ import annotations


def get_session_key(channel: str, chat_id: str) -> str:
    """Return the canonical session key 'channel:chat_id'."""
    return f"{channel}:{chat_id}"


def inject_history_before_user_message(message: str, history_context: str) -> str:
    """Insert history_context before the '用户消息:' marker, or prepend it."""
    if not history_context:
        return message
    user_msg_marker = "用户消息:"
    if user_msg_marker in message:
        idx = message.find(user_msg_marker)
        return message[:idx] + history_context + "\n\n" + message[idx:]
    return f"{history_context}\n\n{message}"


def estimate_tokens(text: str) -> int:
    """Rough token count: 4 chars ≈ 1 token (mixed Chinese/English)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def clip_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, appending '...' if truncated."""
    clean = (text or "").strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip() + "..."


def is_context_overflow_error(error_text: str) -> bool:
    """Return True if error_text looks like a context/token overflow."""
    text = (error_text or "").lower()
    keywords = [
        "context",
        "token",
        "too long",
        "max_tokens",
        "max token",
        "exceed",
        "length",
    ]
    return any(keyword in text for keyword in keywords)
