"""
EduTutor — SQLite persistence сессий (Фаза 2).

Сохранение и восстановление состояния TutorState в SQLite.
Каждая сессия хранит полный граф состояния; автосохранение
при каждом изменении состояния через callback.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("edututor.session_store")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_activity REAL NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_activity ON sessions(last_activity);
"""


class SessionSQLiteStore:
    """Персистентное хранилище сессий на SQLite.

    Используется как надстройка над in-memory EngineStore для
    долговременного сохранения состояний между перезапусками сервера.
    """

    def __init__(self, db_path: Path, ttl_sec: float = 1800.0):
        self._db_path = db_path
        self._ttl_sec = ttl_sec
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
        self._local = threading.local()

    def _get_conn(self) -> sqlite3.Connection:
        """Получить потокобезопасное соединение."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self._db_path))
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def save(self, session_id: str, state_dict: Dict[str, Any]) -> None:
        """Сохранить состояние сессии."""
        conn = self._get_conn()
        now = time.monotonic()
        created = getattr(self._local, "_save_created", now)
        try:
            conn.execute(
                """INSERT INTO sessions (id, state_json, created_at, last_activity)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       state_json=excluded.state_json,
                       last_activity=excluded.last_activity,
                       version=version+1""",
                (session_id, json.dumps(state_dict, ensure_ascii=False), created, now),
            )
            conn.commit()
        except Exception:
            logger.exception("Ошибка сохранения сессии %s", session_id)

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Загрузить состояние сессии по ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT state_json FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def exists(self, session_id: str) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None

    def delete(self, session_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def list_ids(self) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id FROM sessions ORDER BY last_activity DESC"
        ).fetchall()
        return [r[0] for r in rows]

    def sweep_expired(self, now: Optional[float] = None) -> int:
        """Удалить сессии старше TTL."""
        conn = self._get_conn()
        cutoff = (now or time.monotonic()) - self._ttl_sec
        cursor = conn.execute(
            "DELETE FROM sessions WHERE last_activity < ?", (cutoff,)
        )
        conn.commit()
        return cursor.rowcount

    def close(self):
        conn = self._get_conn()
        try:
            conn.close()
        except Exception:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def save_session(session_store: SessionSQLiteStore, session_id: str, state: Any) -> None:
    """Callback-утилита для сохранения состояния после каждого шага графа."""
    try:
        session_store.save(session_id, state.model_dump())
        # Отмечаем created при первом сохранении
        if not hasattr(session_store._local, "_save_created"):
            setattr(session_store._local, "_save_created", time.monotonic())
    except Exception:
        logger.exception("Не удалось сохранить сессию %s", session_id)


def restore_state(session_store: SessionSQLiteStore, session_id: str) -> Optional[Dict[str, Any]]:
    """Восстановить предыдущее состояние сессии из SQLite."""
    return session_store.load(session_id)
