"""Process-local integration fixture. Each authenticated user owns an isolated in-memory DB.

The dict chooses the user's store; the same SQLite state machine as production
runs inside it so confirmation, expiry, retries and status codes stay identical.
Nothing is persisted across fixture instances or process restarts.
"""
from contextlib import contextmanager
from threading import RLock
import time

from .memory import MemoryStore, identity


class DictMemoryStore:
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self._users = {}
        self._lock = RLock()

    @contextmanager
    def _for_user(self, user_id):
        uid = identity(user_id)
        with self._lock:
            if uid not in self._users:
                self._users[uid] = MemoryStore(":memory:", clock=self.clock)
            yield self._users[uid]

    def record_source(self, user_id, session_id, turn_id, input_id, text, *,
                      save_authorized=False, authorized_text=None):
        with self._for_user(user_id) as store:
            return store.record_source(user_id, session_id, turn_id, input_id, text,
                                       save_authorized=save_authorized,
                                       authorized_text=authorized_text)

    def list_memories(self, user_id):
        with self._for_user(user_id) as store:
            return store.list_memories(user_id)

    def apply_memory(self, user_id, cmd):
        return self.apply_memory_with_status(user_id, cmd)[0]

    def apply_memory_with_status(self, user_id, cmd):
        with self._for_user(user_id) as store:
            return store.apply_memory_with_status(user_id, cmd)

    def close(self):
        with self._lock:
            for store in self._users.values():
                store.close()
            self._users.clear()