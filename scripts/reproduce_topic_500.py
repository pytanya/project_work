"""Воспроизводит сценарий пользователя: intake → upload → topic selection.

Запуск: python scripts/reproduce_topic_500.py
"""
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 1) Создаём сессию
r = requests.post(f"{BASE}/api/sessions", json={})
print("Create session:", r.status_code, r.text[:100])
sid = r.json()["session_id"]

# 2) Проходим intake
answers = ["ученик 4 класса", "4", "основы православной культуры", "Культура и религия", "да", "квиз"]
for a in answers:
    r = requests.post(f"{BASE}/api/sessions/{sid}/intake", json={"answer": a})
    print(f"Intake '{a}':", r.status_code)
    time.sleep(0.5)

# 3) Проверяем status - агент просит загрузить файл
r = requests.get(f"{BASE}/api/sessions/{sid}/intake/status")
print("Intake status:", r.json())

# 4) Загружаем учебник
pdf = Path(r"C:\otus\project_work\data\uploads\fcde261d.pdf")
if pdf.exists():
    with pdf.open("rb") as f:
        r = requests.post(
            f"{BASE}/api/sessions/{sid}/upload",
            files={"file": (pdf.name, f, "application/pdf")},
        )
    print("Upload:", r.status_code, r.text[:200])
else:
    print("PDF not found:", pdf)
    sys.exit(1)

# 5) Ждём индексацию и граф
for i in range(40):
    time.sleep(3)
    r = requests.get(f"{BASE}/api/sessions/{sid}/graph")
    g = r.json()
    print(f"[{i}] graph nodes:", len(g.get("nodes", [])))
    if len(g.get("nodes", [])) >= 2:
        break

# 6) Выбираем первую тему
nodes = g.get("nodes", [])
topics = [n for n in nodes if n.get("type") != "book"]
if topics:
    topic_id = topics[0]["id"]
    print("Selecting topic:", topics[0].get("title"), topic_id)
    r = requests.post(f"{BASE}/api/sessions/{sid}/topic", json={"topic_id": topic_id})
    print("Select topic:", r.status_code)
    print("Body:", r.text[:500])
else:
    print("No topics found in graph")
