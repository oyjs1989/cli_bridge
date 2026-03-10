"""Session management module for cli-bridge.

Provides session tracking and management across different chat channels.
"""

from cli_bridge.session.manager import SessionManager, SessionMetadata

__all__ = [
    "SessionManager",
    "SessionMetadata",
]
