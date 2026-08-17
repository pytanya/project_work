"""Проверяет восстановление сессии из SQLite и выбор темы."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.session_store import SessionSQLiteStore
from api.engine import SessionStore, run_step
import asyncio


async def main():
    store = SessionStore()
    print("SQLite initialized:", store._sqlite is not None)
    if store._sqlite is None:
        print("SQLite FAILED - отключаем проверку")
        return

    # Проверяем есть ли сохранённые сессии
    ids = store._sqlite.list_ids()
    print("Saved sessions:", ids)
    for sid in ids[:3]:
        saved = store._sqlite.load(sid)
        if saved:
            print(f"\n=== Session {sid} ===")
            print("  active_topic:", saved.get("active_topic"))
            print("  source_status:", saved.get("source_status"))
            print("  knowledge_graph nodes:", len((saved.get("knowledge_graph") or {}).get("nodes", [])))
            print("  quiz_complete:", saved.get("quiz_complete"))

            # Восстанавливаем
            session = store.restore_or_create()
            print("  Restored session id:", session.id)
            print("  current_question:", session.state.current_question)

            # Пробуем выбрать тему если есть граф
            kg = session.state.knowledge_graph or {}
            nodes = kg.get("nodes", [])
            topics = [n for n in nodes if n.get("type") != "book"]
            if topics:
                t = topics[0]
                print(f"  Selecting topic: {t.get('title')}")
                try:
                    from api.routes.graph import select_topic
                    # Вызываем логику напрямую
                    await run_step(session, answer=None)
                    print("  run_step OK")
                except Exception as e:
                    import traceback
                    print("  ERROR:", e)
                    traceback.print_exc()
            break


asyncio.run(main())
