"""Проверяет выбор КАЖДОЙ темы из графа, ищет падающую."""
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Восстанавливаем сессию с графом (4d29a9cbe61b уже имеет 31 узел)
sid = "4d29a9cbe61b"

r = requests.get(f"{BASE}/api/sessions/{sid}/graph")
g = r.json()
nodes = g.get("nodes", [])
topics = [n for n in nodes if n.get("type") != "book"]
print(f"Found {len(topics)} topics")
for i, t in enumerate(topics):
    topic_id = t["id"]
    title = t.get("title", "")
    print(f"\n[{i}] Selecting: {title} ({topic_id})")
    r = requests.post(f"{BASE}/api/sessions/{sid}/topic", json={"topic_id": topic_id})
    status = r.status_code
    body = r.text[:150]
    print(f"    Status: {status} | {body}")
    if status >= 500:
        print(f"    >>> FAILED ON TOPIC: {title}")
        break
    time.sleep(1)
