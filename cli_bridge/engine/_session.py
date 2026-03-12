"""Session mapping utilities for the IFlow adapter.

Contains:
- SessionMappingManager: persists channel:chat_id → iflow session-id mappings
- list_iflow_sessions_for_dir(): reads iflow session files from a directory
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger


class SessionMappingManager:
    """管理渠道用户到 iflow 会话 ID 的映射。

    存储格式:
    {
        "telegram:123456": "session-abc123...",
        "discord:789012": "session-def456...",
    }
    """

    def __init__(self, mapping_file: Path | None = None):
        self.mapping_file = mapping_file or Path.home() / ".cli-bridge" / "session_mappings.json"
        self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
        self._mappings: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.mapping_file.exists():
            try:
                with open(self.mapping_file, encoding="utf-8") as f:
                    self._mappings = json.load(f)
                logger.debug(f"Loaded {len(self._mappings)} session mappings")
            except json.JSONDecodeError:
                logger.warning("Invalid mapping file, starting fresh")
                self._mappings = {}

    def _save(self) -> None:
        with open(self.mapping_file, "w", encoding="utf-8") as f:
            json.dump(self._mappings, f, indent=2, ensure_ascii=False)

    def get_session_id(self, channel: str, chat_id: str) -> str | None:
        key = f"{channel}:{chat_id}"
        return self._mappings.get(key)

    def set_session_id(self, channel: str, chat_id: str, session_id: str) -> None:
        key = f"{channel}:{chat_id}"
        self._mappings[key] = session_id
        self._save()
        logger.debug(f"Session mapping: {key} -> {session_id}")

    def clear_session(self, channel: str, chat_id: str) -> bool:
        key = f"{channel}:{chat_id}"
        if key in self._mappings:
            del self._mappings[key]
            self._save()
            return True
        return False

    def list_all(self) -> dict[str, str]:
        return self._mappings.copy()


def list_iflow_sessions_for_dir(sessions_dir: Path) -> list[dict]:
    """Read iflow session metadata from a project sessions directory.

    Args:
        sessions_dir: Path to the iflow project sessions directory
                      (typically ~/.iflow/projects/{hash}/)

    Returns:
        List of session metadata dicts, sorted by updated_at descending.
    """
    if not sessions_dir.exists():
        return []

    sessions = []
    for session_file in sessions_dir.glob("session-*.jsonl"):
        try:
            stat = session_file.stat()
            session_id = session_file.stem

            with open(session_file, encoding="utf-8") as f:
                lines = f.readlines()
                message_count = len([line for line in lines if line.strip()])

            first_msg = None
            last_msg = None
            if lines:
                try:
                    first = json.loads(lines[0])
                    first_msg = first.get("timestamp")
                    last = json.loads(lines[-1])
                    last_msg = last.get("timestamp")
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass

            sessions.append(
                {
                    "id": session_id,
                    "file": str(session_file),
                    "created_at": first_msg or datetime.fromtimestamp(stat.st_ctime).isoformat(),
                    "updated_at": last_msg or datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "message_count": message_count,
                }
            )
        except Exception as e:
            logger.debug(f"Error reading session {session_file}: {e}")

    sessions.sort(key=lambda x: x["updated_at"], reverse=True)
    return sessions
