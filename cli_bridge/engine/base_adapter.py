"""Base adapter abstract class for cli-bridge backends."""

from abc import ABC, abstractmethod
from collections.abc import Callable


class BaseAdapter(ABC):
    """Abstract base class defining the contract for all backend adapters.

    Any backend (iflow, Claude Code, or future backends) MUST implement
    this interface. This is the sole coupling point between AgentLoop
    and backend-specific code.
    """

    # ----- Identity -----

    @property
    def inline_agents(self) -> bool:
        """Whether AGENTS.md should be injected inline into each message.

        Returns True for short-lived (cli) adapters where there is no
        persistent session to carry the system context.  Long-lived adapters
        (stdio, acp, claude) override this to False because the session
        system_prompt already carries the agent context.
        """
        return True

    # ----- Core Chat -----

    @abstractmethod
    async def chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Send a message and return the full response text."""
        ...

    @abstractmethod
    async def chat_stream(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
        on_chunk: Callable | None = None,
        on_tool_call: Callable | None = None,
        on_event: Callable | None = None,
    ) -> str:
        """Send a message with streaming callbacks. Returns final response text."""
        ...

    @abstractmethod
    async def new_chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
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
