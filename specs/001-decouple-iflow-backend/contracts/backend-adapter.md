# Contract: Backend Adapter Interface

**Type**: Internal Python ABC
**Location**: `cli_bridge/engine/base_adapter.py` (new file)
**Date**: 2026-03-11

---

## Purpose

`BaseAdapter` defines the contract that `AgentLoop` and all callers rely on. Any backend adapter (iflow, Claude Code, or future backends) MUST implement this interface. This contract is the sole coupling point between the engine core and backend-specific code.

---

## Interface Definition

```python
from abc import ABC, abstractmethod
from typing import Callable, Optional


class BaseAdapter(ABC):

    # ----- Identity -----

    @property
    @abstractmethod
    def mode(self) -> str:
        """Identifier for the active mode (e.g., 'cli', 'stdio', 'acp', 'claude')."""
        ...

    # ----- Core Chat -----

    @abstractmethod
    async def chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Send a message and return the full response text."""
        ...

    @abstractmethod
    async def chat_stream(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        on_chunk: Optional[Callable] = None,
        on_tool_call: Optional[Callable] = None,
        on_event: Optional[Callable] = None,
    ) -> str:
        """Send a message with streaming callbacks. Returns final response text."""
        ...

    @abstractmethod
    async def new_chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Start a new conversation session, discarding prior history, then send message."""
        ...

    # ----- Session Management -----

    @abstractmethod
    def clear_session(self, channel: str, chat_id: str) -> bool:
        """Clear the session for the given channel+chat_id. Returns True if cleared."""
        ...

    # ----- Lifecycle -----

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the backend is reachable and operational."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release all resources (processes, connections, locks)."""
        ...
```

---

## Callback Signatures

### `on_chunk(channel: str, chat_id: str, chunk_text: str) -> Coroutine`
Called for each streaming text chunk. `chunk_text` accumulates progressively.

### `on_tool_call(tool_call: ToolCall) -> Coroutine`
Called when a tool invocation is initiated.

### `on_event(event: dict) -> Coroutine`
Called for structured events. Current supported type:
```json
{"type": "plan", "entries": [{"content": "...", "status": "..."}]}
```

---

## Compliance

| Adapter | Status |
|---------|--------|
| `IFlowAdapter` | Must be updated to implement `clear_session()` at the meta level |
| `ClaudeAdapter` | Already compliant; verify `close()` exists |
| `StdioACPAdapter` | Internal to IFlowAdapter; not a direct `BaseAdapter` implementor |
| `ACPAdapter` | Internal to IFlowAdapter; not a direct `BaseAdapter` implementor |
