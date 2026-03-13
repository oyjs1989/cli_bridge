"""cli-bridge - Multi-channel AI Assistant powered by Claude CLI."""

__version__ = "0.3.5"
__logo__ = "🤖"

from cli_bridge.bus.events import InboundMessage, OutboundMessage
from cli_bridge.bus.queue import MessageBus
from cli_bridge.engine.adapter import IFlowAdapter

__all__ = [
    "__version__",
    "__logo__",
    "IFlowAdapter",
    "MessageBus",
    "InboundMessage",
    "OutboundMessage",
]
