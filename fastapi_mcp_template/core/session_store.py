"""In-memory MCP session store with idle expiry and a size cap.

Sessions live in process memory, so they are lost on restart and are not
shared between worker processes. Clients are told about a lost session with
HTTP 404 and re-initialize, as the MCP Streamable HTTP transport prescribes.
"""

import time
import uuid
from typing import Any, Callable, Dict, Optional


class SessionStore:
    """Tracks active MCP sessions."""

    def __init__(
        self,
        ttl_seconds: float = 3600,
        max_sessions: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max(1, max_sessions)
        self._clock = clock
        # Ordered by last use: the first entry is the least recently used.
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def create(self, protocol_version: str, initialized: bool = False) -> str:
        """Create a session and return its ID."""
        self._prune()
        while len(self._sessions) >= self.max_sessions:
            # Full even after dropping expired sessions: evict the least
            # recently used one rather than refusing new clients.
            self._sessions.pop(next(iter(self._sessions)))

        session_id = str(uuid.uuid4())
        now = self._clock()
        self._sessions[session_id] = {
            "created": now,
            "last_seen": now,
            "initialized": initialized,
            "protocol_version": protocol_version,
        }
        return session_id

    def get(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Return a live session and mark it as used, or ``None``."""
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        now = self._clock()
        if self._is_expired(session, now):
            del self._sessions[session_id]
            return None
        session["last_seen"] = now
        # Move to the end so eviction order follows recency of use.
        self._sessions[session_id] = self._sessions.pop(session_id)
        return session

    def delete(self, session_id: Optional[str]) -> bool:
        """Remove a session. Returns whether it existed."""
        if not session_id:
            return False
        return self._sessions.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._sessions

    def _is_expired(self, session: Dict[str, Any], now: float) -> bool:
        return self.ttl_seconds > 0 and now - session["last_seen"] > self.ttl_seconds

    def _prune(self) -> None:
        now = self._clock()
        expired = [sid for sid, s in self._sessions.items() if self._is_expired(s, now)]
        for sid in expired:
            del self._sessions[sid]
