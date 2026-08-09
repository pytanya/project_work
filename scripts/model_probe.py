"""
model_probe.py — тест-колл доступности моделей EduTutor (Этап 0).

Проверяет (через OpenAI-совместимые endpoints):
  - TUTOR_MODEL (qwen/qwen3.7-flash) на RouterAI (работает в РФ)
  - EXPERT_MODEL (deepseek/deepseek-v4-flash) на RouterAI
  - CHEAP_MODEL (qwen/qwen2.5-flash) на RouterAI
  - JUDGE_MODEL (google/gemini-3.5-flash-lite) через OpenRouter — ТОЛЬКО под VPN
  - JUDGE fallback (google/gemini-3.1-flash-lite) через OpenRouter — ТОЛЬКО под VPN
  - Yandex Search API (поисковый ключ)

Без зависимостей: .env читается вручную, используется только requests.
Ключи НЕ выводятся в лог. Результат — model_probe_results_<ts>.json (в .gitignore).

Запуск:
    python scripts/model_probe.py
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

_TIMEOUT = 60


def load_env(path: Path) -> dict[str, str]:
    """Примитивный парсер .env (KEY=VALUE, #комментарии, кавычки)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        elif val.startswith("'") and val.endswith("'"):
            val = val[1:-1]
        if key:
            env[key] = val
    return env


def probe_chat(
    base_url: str,
    api_key: str,
    model: str,
    *,
    extra_headers: dict | None = None,
) -> dict:
    """Один тест-колл chat.completions. Возвращает результат проверки."""
    import requests

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты — помощник. Отвечай одним словом."},
            {"role": "user", "content": "Привет. Ответь: ок."},
        ],
        "max_tokens": 8,
        "temperature": 0,
    }
    started = datetime.datetime.now()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
        elapsed = round((datetime.datetime.now() - started).total_seconds(), 1)
        if resp.status_code == 200:
            data = resp.json()
            content = ""
            try:
                content = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                pass
            return {
                "ok": True,
                "status": 200,
                "elapsed_sec": elapsed,
                "reply": content[:60] or "(пустой ответ)",
                "model": model,
            }
        err_text = ""
        try:
            err = resp.json()
            err_text = err.get("error", {}).get("message", "") or str(err)[:200]
        except Exception:
            err_text = resp.text[:200]
        return {
            "ok": False,
            "status": resp.status_code,
            "elapsed_sec": elapsed,
            "error": err_text[:200],
            "model": model,
        }
    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"timeout > {_TIMEOUT}s", "model": model}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": str(e)[:200], "model": model}


def probe_yandex_search(api_key: str, folder_id: str) -> dict:
    """Тест-колл Yandex Search API v2 (поисковый ключ)."""
    import requests

    url = "https://searchapi.api.cloud.yandex.net/v2/web/search"
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": "учебник география 6 класс"},
        "folderId": folder_id,
        "responseFormat": "FORMAT_XML",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            return {"ok": True, "status": 200, "note": "Yandex Search API доступен (поисковый ключ)"}
        return {"ok": False, "status": resp.status_code, "error": resp.text[:200]}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": str(e)[:200]}


def main() -> int:
    env = load_env(BASE_DIR / ".env")
    results: dict = {"timestamp": datetime.datetime.now().isoformat(timespec="seconds"), "probes": []}
    console_lines: list[str] = []

    def add(name: str, r: dict) -> None:
        results["probes"].append({"name": name, **r})
        tag = "OK " if r.get("ok") else "ERR"
        console_lines.append(
            f"[{tag}] {name}: {r.get('model', '')} — {r.get('status', r.get('error', ''))} ({r.get('elapsed_sec', '-')}s)"
        )

    routerai_key = env.get("ROUTERAI_API_KEY", "").strip()
    routerai_base = env.get("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1").strip()
    openrouter_key = env.get("OPENROUTER_API_KEY", "").strip()
    openrouter_base = env.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
    yandex_key = env.get("YANDEX_API_KEY", "").strip()
    yandex_folder = env.get("YANDEX_FOLDER_ID", "").strip()

    print("=" * 60)
    print("EduTutor — тест-колл доступности моделей (Этап 0)")
    print("=" * 60)

    # RouterAI — работает в РФ, ключ заполнен
    if routerai_key:
        for model in [
            env.get("TUTOR_MODEL", "qwen/qwen3.7-flash"),
            env.get("EXPERT_MODEL", "deepseek/deepseek-v4-flash"),
            env.get("CHEAP_MODEL", "qwen/qwen2.5-flash"),
        ]:
            add(f"routerai:{model}", probe_chat(routerai_base, routerai_key, model))
    else:
        console_lines.append("[SKIP] RouterAI: ROUTERAI_API_KEY не задан")

    # OpenRouter (судья gemini) — ЗАБЛОКИРОВАН в РФ, работает только под VPN
    if openrouter_key:
        fallback_first = env.get("JUDGE_FALLBACK_MODELS", "google/gemini-3.1-flash-lite").split(",")[0].strip()
        for model in [
            env.get("JUDGE_MODEL", "google/gemini-3.5-flash-lite"),
            fallback_first,
        ]:
            add(
                f"openrouter:{model}",
                probe_chat(
                    openrouter_base,
                    openrouter_key,
                    model,
                    extra_headers={"HTTP-Referer": "http://localhost", "X-Title": "EduTutor-probe"},
                ),
            )
    else:
        console_lines.append("[SKIP] OpenRouter: OPENROUTER_API_KEY не задан (судья недоступен без VPN)")

    # Yandex Search (поисковый ключ)
    if yandex_key and yandex_folder:
        add("yandex_search", probe_yandex_search(yandex_key, yandex_folder))
    else:
        console_lines.append("[SKIP] Yandex Search: YANDEX_API_KEY/YANDEX_FOLDER_ID не заданы")

    for line in console_lines:
        print(line)
    print("=" * 60)

    ok_count = sum(1 for p in results["probes"] if p.get("ok"))
    print(f"Итого: {ok_count}/{len(results['probes'])} доступных")
    if not ok_count:
        print("Все модели недоступны. Проверьте: интернет, ключи, VPN (для OpenRouter/gemini).")

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BASE_DIR / f"model_probe_results_{ts}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Результат: {out.name} (не коммитится)")

    return 0 if ok_count == len(results["probes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
