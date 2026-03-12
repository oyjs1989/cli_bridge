"""StreamingMixin — shared metadata-flag helpers for streaming-capable channels.

Does NOT add abstract methods to BaseChannel. Channels that support streaming
can inherit from this mixin to get common flag-detection helpers.
"""
from __future__ import annotations

from cli_bridge.bus.events import OutboundMessage


class StreamingMixin:
    """Mixin providing helpers for the _streaming / _streaming_end / _progress flags.

    Usage::

        class MyChannel(StreamingMixin, BaseChannel):
            async def send(self, msg):
                if self._is_streaming_or_end(msg):
                    await self._handle_streaming_message(...)
                    return
                if self._is_progress(msg):
                    return
                # normal send path
    """

    @staticmethod
    def _is_streaming_or_end(msg: OutboundMessage) -> bool:
        """Return True if message carries _streaming or _streaming_end flag."""
        return bool(msg.metadata.get("_streaming") or msg.metadata.get("_streaming_end"))

    @staticmethod
    def _is_progress(msg: OutboundMessage) -> bool:
        """Return True if message carries _progress flag only (no streaming)."""
        return bool(msg.metadata.get("_progress"))

    @staticmethod
    def _is_streaming_end(msg: OutboundMessage) -> bool:
        """Return True if message carries the final _streaming_end flag."""
        return bool(msg.metadata.get("_streaming_end"))

    @staticmethod
    def _is_streaming_chunk(msg: OutboundMessage) -> bool:
        """Return True if message is an intermediate streaming chunk."""
        return bool(msg.metadata.get("_streaming") and not msg.metadata.get("_streaming_end"))
