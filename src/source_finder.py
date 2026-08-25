"""
EduTutor — сбор учебных материалов (разделы 6.1–6.3).

- search_web: каскад primary → yandex/tavily → stepik → lesson_edu → ddgs.
  stepik (публичный REST API) и lesson.edu.ru (Минпросвещения, JS-каталог) —
  легальные источники РФ (roadmap #4), работают по белому списку без ключей.
- fetch_url / fetch_html: безопасная загрузка (SSRF-защита is_url_blocked, К-2).
- license_check: проверка лицензионной чистоты источника (К-2, 6.3).
- find_local_textbooks: локальные PDF-учебники из TEXTBOOKS_DOWNLOADS_DIR (Plan B, В-2).
- download_file / verify_textbook: скачивание/валидация легально доступных файлов.
- collect_source_materials: fallback-цепочка источников (6.2) + узел source_failed (В-3).
- crawl_page_js / crawl_textbook_catalog: crawl4ai (lazy, ставится на Слайсе 5b);
  dynamic rendering БЕЗ обхода капчи/аутентификации/ToS (К-2).
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import re
import socket
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

import httpx

warnings.filterwarnings("ignore", message=".*ddgs.*")

from . import config
from .config import settings as default_settings

logger = logging.getLogger("edututor.source")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 EduTutor/0.1"
)

SEARCH_RESULTS = 5
SEARCH_CONNECT_TIMEOUT = 10.0

# --- SSRF denylist (из research_guard_agent tools.py) ---
_FETCH_DENYLIST_PREFIXES: Tuple[str, ...] = (
    "http://localhost", "https://localhost",
    "http://127.0.0.1", "https://127.0.0.1",
    "http://0.0.0.0", "https://0.0.0.0",
    "http://10.", "https://10.",
    "http://172.16.", "https://172.16.",
    "http://172.17.", "https://172.17.",
    "http://172.18.", "https://172.18.",
    "http://172.19.", "https://172.19.",
    "http://172.20.", "https://172.20.",
    "http://172.21.", "https://172.21.",
    "http://172.22.", "https://172.22.",
    "http://172.23.", "https://172.23.",
    "http://172.24.", "https://172.24.",
    "http://172.25.", "https://172.25.",
    "http://172.26.", "https://172.26.",
    "http://172.27.", "https://172.27.",
    "http://172.28.", "https://172.28.",
    "http://172.29.", "https://172.29.",
    "http://172.30.", "https://172.30.",
    "http://172.31.", "https://172.31.",
    "http://192.168.", "https://192.168.",
    "http://169.254.", "https://169.254.",
    "http://[::1]", "https://[::1]",
    "http://[0:0:0:0:0:0:0:1]", "https://[0:0:0:0:0:0:0:1]",
    "http://169.254.169.254", "https://169.254.169.254",
    "http://metadata.google.internal", "https://metadata.google.internal",
)

# --- Лицензионная политика (К-2, 6.3) ---
# Прямое скачивание файлов — только с открытых/лицензионно допустимых хостов.
ALLOWED_DOWNLOAD_HOSTS = (
    "wikibooks.org", "wikipedia.org", "openbooks", "openedu.ru", "school-collection.edu.ru",
)
# Каталоги-«склады» — ТОЛЬКО источник ссылок, прямое скачивание запрещено.
SCRAPER_DOMAINS = ("11klassov.net", "reshak.ru", "gdz", "obuchalka", "vklasse")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class SourceCollection:
    """Результат сбора материалов (6.2): ready / partial / failed."""

    status: str  # "ready" | "partial" | "failed"
    sources: List[Dict[str, Any]] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)
    message: str = ""
    failed_reason: str = ""  # empty_result | license_blocked | search_timeout | ...


# ----------------------------------------------------------------------
# SSRF-защита
# ----------------------------------------------------------------------
def _block_reason_for_ip(ip: str) -> Optional[str]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    ):
        return f"IP {ip} относится к внутренним/приватным сетям"
    return None


def _parse_numeric_host(host: str) -> Optional[str]:
    def _val(p: str) -> int:
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", p):
            return int(p, 16)
        if p.isdigit():
            if len(p) > 1 and p.startswith("0") and all(c in "01234567" for c in p):
                return int(p, 8)
            return int(p)
        raise ValueError(p)

    h = host.strip().lower()
    if not h:
        return None
    if re.fullmatch(r"(0[xX])?[0-9a-fA-F]+", h):
        try:
            val = _val(h)
        except ValueError:
            return None
        if 0 <= val <= 0xFFFFFFFF:
            return str(ipaddress.ip_address(val))
        return None
    parts = h.split(".")
    if 2 <= len(parts) <= 4:
        try:
            vals = [_val(p) for p in parts]
        except ValueError:
            return None
        if any(v > 0xFFFFFFFF for v in vals):
            return None
        tail = vals.pop()
        if tail > 0xFFFFFF:
            return None
        tail_bytes: List[int] = [tail & 0xFF]
        t = tail >> 8
        while t:
            tail_bytes.append(t & 0xFF)
            t >>= 8
        if len(vals) + len(tail_bytes) > 4:
            return None
        octets = vals + [0] * (4 - len(vals) - len(tail_bytes)) + tail_bytes[::-1]
        return ".".join(str(o) for o in octets)
    return None


def _resolve_host_ips(host: str) -> List[str]:
    h = host.strip("[]")
    try:
        infos = socket.getaddrinfo(h, None)
    except socket.gaierror:
        return []
    ips: List[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in ips:
            ips.append(addr)
    return ips


def is_url_blocked(url: str) -> Optional[str]:
    """SSRF-защита (3 уровня). Возвращает причину блокировки или None."""
    url_lower = url.lower().rstrip("/")
    for prefix in _FETCH_DENYLIST_PREFIXES:
        if url_lower.startswith(prefix.lower()):
            return f"URL заблокирован (внутренний/приватный адрес): {prefix}*"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "URL не удалось распарсить"
    host = parsed.hostname
    if not host:
        return "URL не содержит hostname"
    numeric = _parse_numeric_host(host)
    if numeric is not None:
        reason = _block_reason_for_ip(numeric)
        if reason:
            return f"URL заблокирован: {reason} (host: {host})"
        return None
    for ip in _resolve_host_ips(host):
        reason = _block_reason_for_ip(ip)
        if reason:
            return f"URL заблокирован: {reason} (host: {host})"
    return None


# ----------------------------------------------------------------------
# License check (К-2, 6.3)
# ----------------------------------------------------------------------
def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_in(host: str, suffixes: Tuple[str, ...]) -> bool:
    host = host.lower().lstrip("www.")
    return any(host == s or host.endswith("." + s) for s in suffixes)


def license_check(url: str, for_download: bool = False) -> Tuple[bool, str]:
    """Проверка лицензионной чистоты источника (К-2).

    - for_download=True: разрешено только с ALLOWED_DOWNLOAD_HOSTS (прямое скачивание
      файлов); каталоги-«склады» запрещены.
    - for_download=False: страница текста — публичные хосты допустимы, «склады»
      помечаются как ссылочные (контент не скачиваем).
    """
    host = host_of(url)
    if not host:
        return False, "URL не содержит hostname"
    if _host_in(host, SCRAPER_DOMAINS):
        return (False, f"Хост {host} — каталог-«склад», используется только как источник ссылок")
    if for_download and not _host_in(host, ALLOWED_DOWNLOAD_HOSTS):
        return False, f"Хост {host} не входит в список лицензионно допустимых для скачивания"
    return True, "источник лицензионно допустим"


# ----------------------------------------------------------------------
# Кэш источников (6.3)
# ----------------------------------------------------------------------
def _cache_path(url: str, cache_dir: Path) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{h}.txt"


def _cache_read(url: str, cache_dir: Path) -> Optional[str]:
    p = _cache_path(url, cache_dir)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


def _cache_write(url: str, cache_dir: Path, text: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(url, cache_dir).write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
def _get_text(url: str, client: httpx.Client, max_chars: int) -> Tuple[str, str]:
    """Загрузка страницы (SSRF-safe, редиректы ≤5). Возвращает (text, status)."""
    current = url
    for _hop in range(6):
        block = is_url_blocked(current)
        if block:
            logger.warning("fetch: заблокирован %s — %s", current, block)
            return block, "ERROR"
        resp = client.get(
            current,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
            follow_redirects=False,
        )
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return "Ошибка: редирект без Location", "ERROR"
            current = urljoin(current, location)
            continue
        break
    resp.raise_for_status()
    body = resp.content
    text = body.decode("utf-8", errors="replace")
    return text, "OK"


def _strip_html(raw: str) -> str:
    """Очистка HTML до текста с СОХРАНЕНИЕМ структуры (заголовки/абзацы/списки).

    Раньше весь текст схлопывался в одну строку → extract_sections и граф знаний
    не видели структуру веб-конспектов (только один generic-узел «topic»).
    Теперь: блочные теги → перенос строки, заголовки h1-h6 → маркдаун-заголовки,
    остальные теги убираем, внутри строки пробелы схлопываем.
    """
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    # блочные границы → перенос строки
    text = re.sub(r"(?i)</(p|div|li|ul|ol|tr|table|blockquote|section|article|h[1-6])>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    # заголовки h1-h6 → маркдаун-заголовок (в любом месте строки)
    text = re.sub(r"(?i)</h([1-6])>\s*$", lambda m: "  \n" + ("#" * int(m.group(1))) + " ", text)
    text = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "#" * int(m.group(1)) + " ", text)
    # убрать оставшиеся теги
    text = re.sub(r"<[^>]+>", "", text)
    # декодировать базовые сущности
    text = text.replace("&nbsp;", " ").replace("&mdash;", "—").replace("&ndash;", "–")
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&laquo;", "«").replace("&raquo;", "»")
    # схлопнуть пробелы внутри строк, сохранив переводы строк
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]
    text = "\n".join(l for l in lines if l)
    return text


def fetch_url(url: str, client: Optional[httpx.Client] = None) -> Tuple[str, str]:
    """Загрузка страницы как ОЧИЩЕННОГО текста (MAX_FETCH_CHARS=32000, К-3)."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Ошибка: URL должен начинаться с http:// или https://", "ERROR"
    close = False
    if client is None:
        client = httpx.Client(timeout=default_settings.REQUEST_TIMEOUT)
        close = True
    try:
        # Провайдеры с API/JS-рендерингом: обычный HTML-fetch не даст контент.
        if "stepik.org/course/" in url:
            return _fetch_stepik_text(url, client)
        if _LESSON_EDU_HOST in url:
            return _fetch_lesson_edu_text(url, default_settings)
        raw, status = _get_text(url, client, max_chars=default_settings.MAX_FETCH_CHARS)
        if status != "OK":
            return raw, status
        text = _strip_html(raw)
        if not text:
            return "Страница не содержит текстового содержимого", "ERROR"
        if len(text) > default_settings.MAX_FETCH_CHARS:
            text = text[: default_settings.MAX_FETCH_CHARS] + "…[обрезано]"
        return text, "OK"
    except httpx.HTTPError as e:
        return f"Ошибка при загрузке {url}: {e}", "ERROR"
    finally:
        if close:
            client.close()


def fetch_html(url: str, client: Optional[httpx.Client] = None) -> Tuple[str, str]:
    """Загрузка ИСХОДНОГО HTML (MAX_FETCH_CHARS_HTML, для парсинга ссылок)."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Ошибка: URL должен начинаться с http:// или https://", "ERROR"
    close = False
    if client is None:
        client = httpx.Client(timeout=default_settings.REQUEST_TIMEOUT)
        close = True
    try:
        raw, status = _get_text(url, client, max_chars=default_settings.MAX_FETCH_CHARS_HTML)
        if status != "OK":
            return raw, status
        if len(raw) > default_settings.MAX_FETCH_CHARS_HTML:
            raw = raw[: default_settings.MAX_FETCH_CHARS_HTML]
        return raw, "OK"
    except httpx.HTTPError as e:
        return f"Ошибка при загрузке {url}: {e}", "ERROR"
    finally:
        if close:
            client.close()


# ----------------------------------------------------------------------
# Поиск (каскад yandex → tavily → ddgs)
# ----------------------------------------------------------------------
def _search_yandex(query: str, settings: Any) -> List[SearchResult]:
    if not settings.YANDEX_API_KEY or not settings.YANDEX_FOLDER_ID:
        raise RuntimeError("YANDEX_API_KEY/YANDEX_FOLDER_ID не настроены")
    url = "https://searchapi.api.cloud.yandex.net/v2/web/search"
    headers = {"Authorization": f"Api-Key {settings.YANDEX_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query},
        "folderId": settings.YANDEX_FOLDER_ID,
        "responseFormat": "FORMAT_XML",
    }
    resp = httpx.post(url, json=payload, headers=headers, timeout=(SEARCH_CONNECT_TIMEOUT, settings.REQUEST_TIMEOUT))
    resp.raise_for_status()
    data = resp.json()
    raw_b64 = data.get("rawData", "")
    if not raw_b64:
        raise RuntimeError("Yandex Search вернул пустой rawData")
    xml_text = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
    results: List[SearchResult] = []
    for doc in re.findall(r"<doc[^>]*>(.*?)</doc>", xml_text, flags=re.S):
        m_url = re.search(r"<url>(.*?)</url>", doc, flags=re.S)
        m_title = re.search(r"<title>(.*?)</title>", doc, flags=re.S)
        m_snip = re.search(r"<passage>(.*?)</passage>", doc, flags=re.S)
        link = re.sub(r"</?hlword[^>]*>", "", m_url.group(1)).strip() if m_url else ""
        title = re.sub(r"</?hlword[^>]*>", "", m_title.group(1)).strip() if m_title else ""
        snip = re.sub(r"</?hlword[^>]*>", "", m_snip.group(1)).strip() if m_snip else ""
        if link:
            results.append(SearchResult(title=title, url=link, snippet=snip))
        if len(results) >= SEARCH_RESULTS:
            break
    if not results:
        raise RuntimeError("Yandex Search не вернул результатов")
    return results


def _search_tavily(query: str, settings: Any) -> List[SearchResult]:
    if not settings.TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY не настроен")
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": settings.TAVILY_API_KEY, "query": query, "search_depth": "basic",
              "max_results": SEARCH_RESULTS, "include_answer": False},
        timeout=(SEARCH_CONNECT_TIMEOUT, settings.REQUEST_TIMEOUT),
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    if not results:
        raise RuntimeError("Tavily не вернул результатов")
    return [SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("content", "")) for r in results]


def _search_ddgs(query: str, settings: Any) -> List[SearchResult]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    results: List[SearchResult] = []

    def _run() -> None:
        with DDGS(timeout=settings.REQUEST_TIMEOUT) as ddgs:
            for r in ddgs.text(query, max_results=SEARCH_RESULTS):
                results.append(SearchResult(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", "")))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(settings.REQUEST_TIMEOUT)
    if t.is_alive():
        logger.warning("DDGS: поиск не уложился в таймаут — пропускаю fallback")
        return []
    return results


# ----------------------------------------------------------------------
# Провайдеры легальных источников РФ (roadmap #4): Stepik (публичный REST API)
# и lesson.edu.ru (Минпросвещения, JS-каталог через crawl4ai). Работают по
# белому списку/без API-ключей — веб-каталог функционирует там, где
# Wikipedia/ddgs/Yandex недоступны. Капчу/аутентификацию НЕ обходим (К-2).
# ----------------------------------------------------------------------
STEPIK_API = "https://api.stepik.org/api"
_LESSON_EDU_HOST = "lesson.edu.ru"


def _search_stepik(query: str, settings: Any) -> List[SearchResult]:
    """Stepik: поиск курсов по публичному REST API (api.stepik.org/api/courses?query=...).

    Бесплатно и без ключей; курс → материалы (текст-степы) забираем через
    _fetch_stepik_text при fetch_url.
    """
    results: List[SearchResult] = []
    try:
        with httpx.Client(
            timeout=min(settings.REQUEST_TIMEOUT, 15.0),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
        ) as client:
            resp = client.get(f"{STEPIK_API}/courses", params={"query": query})
            resp.raise_for_status()
            courses = (resp.json().get("courses") or [])[:SEARCH_RESULTS]
    except Exception as exc:
        logger.warning("Stepik: поиск курсов недоступен (%s)", exc)
        return []
    for c in courses:
        cid = c.get("id")
        title = (c.get("title") or "").strip()
        if not cid or not title:
            continue
        summary = re.sub(r"<[^>]+>", "", c.get("summary") or "").strip()
        results.append(SearchResult(
            title=f"Stepik: {title}",
            url=f"https://stepik.org/course/{cid}",
            snippet=summary[:300],
        ))
    return results


def _fetch_stepik_text(url: str, client: httpx.Client) -> Tuple[str, str]:
    """Stepik-курс через REST API: course → sections → units → lessons → steps (text-блоки)."""
    m = re.search(r"stepik\.org/course/(\d+)", url)
    if not m:
        return "Ошибка: не URL курса Stepik", "ERROR"
    cid = m.group(1)

    def _api(path: str) -> Dict[str, Any]:
        resp = client.get(
            f"{STEPIK_API}/{path}",
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
        )
        resp.raise_for_status()
        return resp.json()

    try:
        course = (_api(f"courses/{cid}").get("courses") or [{}])[0]
    except Exception as exc:
        # api.stepik.org может быть недоступен (DNS-фильтр), но stepik.org отдаёт
        # server-rendered HTML курса — берём конспект оттуда.
        logger.warning("Stepik: API недоступен (%s) — fallback на HTML-страницу курса", exc)
        try:
            raw, status = _get_text(url, client, max_chars=default_settings.MAX_FETCH_CHARS)
            if status != "OK":
                return f"Stepik: курс недоступен ({raw})", "ERROR"
            text = _strip_html(raw)
            if len(text) < 40:
                return "Stepik: страница курса не содержит текстовых материалов", "ERROR"
            return text[: default_settings.MAX_FETCH_CHARS], "OK"
        except Exception as exc2:
            return f"Stepik: курс недоступен ({exc2})", "ERROR"
    parts = [f"# {course.get('title') or 'Курс'}"]
    if course.get("summary"):
        parts.append(re.sub(r"<[^>]+>", "", course["summary"]).strip())
    try:
        for sid in (course.get("sections") or [])[:2]:
            units = (_api(f"sections/{sid}").get("sections") or [{}])[0].get("units") or []
            for uid in units[:2]:
                lesson = (_api(f"units/{uid}").get("units") or [{}])[0].get("lesson")
                if not lesson:
                    continue
                steps = (_api(f"lessons/{lesson}").get("lessons") or [{}])[0].get("steps") or []
                for step_id in steps[:6]:
                    block = (_api(f"steps/{step_id}").get("steps") or [{}])[0].get("block") or {}
                    if block.get("name") == "text" and block.get("text"):
                        parts.append(_strip_html(block["text"]).strip())
                    if sum(len(p) for p in parts) >= default_settings.MAX_FETCH_CHARS:
                        break
    except Exception as exc:
        logger.warning("Stepik: структура курса недоступна (%s)", exc)
    text = "\n\n".join(p for p in parts if p and p.strip())
    if len(text) < 40:
        return "Stepik: курс не содержит текстовых материалов", "ERROR"
    return text[: default_settings.MAX_FETCH_CHARS], "OK"


def _host_reachable(host: str, settings: Any, timeout: float = 4.0) -> bool:
    """Быстрый probe доступности хоста (белый список/офлайн) перед тяжёлым JS-краулом."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            return client.get(f"https://{host}/").status_code < 500
    except Exception:
        return False


def _crawl_sync(url: str, settings: Any, timeout: float = 25.0) -> Dict[str, Any]:
    """Синхронная обёртка над async crawl_page_js (в потоке, с таймаутом)."""
    result: Dict[str, Any] = {}

    def _run() -> None:
        try:
            import asyncio

            result.update(asyncio.run(crawl_page_js(url, settings)))
        except Exception as exc:
            result["ok"] = False
            result["error"] = str(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return {"ok": False, "error": "timeout"}
    return result


def _search_lesson_edu(query: str, settings: Any) -> List[SearchResult]:
    """lesson.edu.ru (Минпросвещения): JS-каталог через crawl4ai (best-effort, К-2).

    Капчу/аутентификацию не обходим; при недоступности — тихо пустой результат.
    """
    if not _host_reachable(_LESSON_EDU_HOST, settings):
        return []
    q = quote(re.sub(r"[^\w\s-]", "", query)[:60])
    data = _crawl_sync(f"https://{_LESSON_EDU_HOST}/catalog?search={q}", settings)
    md = data.get("markdown") or ""
    results: List[SearchResult] = []
    # Отсекаем ассеты (логотипы/стили) — каталог требует авторизации (401), поэтому
    # в крауле приходят только статические файлы шапки; фильтр защищает от мусора.
    _ASSET_RE = re.compile(r"\.(?:svg|png|jpe?g|gif|webp|css|js|ico|woff2?)(?:\?|$)", re.I)
    for title, href in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", md):
        title = re.sub(r"[#*|]", "", title).strip()
        if not title or len(title) < 3:
            continue
        href = href.strip()
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = f"https://{_LESSON_EDU_HOST}{href}"
        elif not href.startswith("http"):
            continue
        if _LESSON_EDU_HOST not in href or _ASSET_RE.search(href):
            continue
        results.append(SearchResult(title=f"lesson.edu.ru: {title}", url=href))
        if len(results) >= SEARCH_RESULTS:
            break
    return results


def _fetch_lesson_edu_text(url: str, settings: Any) -> Tuple[str, str]:
    """lesson.edu.ru: JS-страница урока → markdown (crawl4ai), best-effort."""
    data = _crawl_sync(url, settings)
    md = (data.get("markdown") or "").strip()
    if not md:
        return "lesson.edu.ru: страница недоступна (капча/авторизация?)", "ERROR"
    return md[: default_settings.MAX_FETCH_CHARS], "OK"


def _search_fipi(query: str, settings: Any) -> List[SearchResult]:
    """ФИПИ: демоверсии/спецификации ОГЭ-ЕГЭ (серверный HTML, HTTP 200).

    Банк заданий ФИПИ закрыт антиботом (403, капчу не обходим — К-2). Лучшее, что
    отдаёт fipi.ru без капчи: страницы демоверсий/кодификаторов по экзамену
    (ОГЭ для 9 класса, иначе ЕГЭ). Opt-in через ENABLE_FIPI.
    """
    low = (query or "").lower()
    exam = "oge" if any(k in low for k in ("оге", "огэ", "9 класс")) else "ege"
    url = f"https://fipi.ru/{exam}/demoversii-specifikacii-kodifikatory"
    return [SearchResult(
        title=f"ФИПИ: демоверсии и спецификации ({exam.upper()})",
        url=url,
        snippet="Официальные демоверсии, спецификации и кодификаторы экзамена по предмету",
    )]


_ENGINES: Dict[str, Callable[[str, Any], List[SearchResult]]] = {
    "yandex": _search_yandex,
    "tavily": _search_tavily,
    "stepik": _search_stepik,
    "lesson_edu": _search_lesson_edu,
    "fipi": _search_fipi,
    "ddgs": _search_ddgs,
}


def search_web(
    query: str,
    engines: Optional[Dict[str, Callable[[str, Any], List[SearchResult]]]] = None,
    settings: Any = None,
) -> List[SearchResult]:
    """Поиск по приоритету (settings.search_engines). Пустой результат — если всё недоступно."""
    s = settings or default_settings
    engines = engines or _ENGINES
    for engine in s.search_engines:
        try:
            results = engines[engine](query, s)
            if results:
                return results
        except Exception as e:
            logger.warning("search_web: %s недоступен (%s)", engine, e)
    return []


# ----------------------------------------------------------------------
# Локальные PDF-учебники (Plan B, В-2)
# ----------------------------------------------------------------------
def find_local_textbooks(
    settings: Any = None,
    subject: Optional[str] = None,
    author: Optional[str] = None,
    grade: Optional[str] = None,
) -> List[Path]:
    """Поиск PDF/DOCX-учебников в TEXTBOOKS_DOWNLOADS_DIR (Plan B для приёмки).

    Совпадение по подстроке в имени файла (предмет/автор/класс, регистр игнор).
    """
    s = settings or default_settings
    directory = Path(s.TEXTBOOKS_DOWNLOADS_DIR)
    if not directory.exists():
        return []
    candidates = [p for p in directory.iterdir() if p.suffix.lower() in (".pdf", ".docx")]
    if not candidates:
        return []
    hints = [x for x in (subject, author, grade) if x]
    if not hints:
        return candidates
    matched = []
    for p in candidates:
        name = p.stem.lower()
        if any(h.lower() in name for h in hints):
            matched.append(p)
    return matched or candidates


# ----------------------------------------------------------------------
# Скачивание и валидация файлов
# ----------------------------------------------------------------------
def download_file(url: str, dest: Path, client: Optional[httpx.Client] = None) -> Optional[Path]:
    """Скачивание PDF/DOCX (SSRF + license_check + сигнатура %PDF + размер)."""
    url = (url or "").strip()
    allowed, reason = license_check(url, for_download=True)
    if not allowed:
        logger.warning("download_file: %s — %s", url, reason)
        return None
    block = is_url_blocked(url)
    if block:
        logger.warning("download_file: %s — %s", url, block)
        return None
    close = False
    if client is None:
        client = httpx.Client(timeout=default_settings.REQUEST_TIMEOUT)
        close = True
    try:
        resp = client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        resp.raise_for_status()
        data = resp.content
        if not data.startswith(b"%PDF") and not data.startswith(b"PK"):  # PK = docx
            logger.warning("download_file: %s — не PDF/DOCX (сигнатура не совпадает)", url)
            return None
        if len(data) > 50 * 1024 * 1024:
            logger.warning("download_file: %s — слишком большой файл", url)
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest
    except httpx.HTTPError as e:
        logger.warning("download_file: %s — %s", url, e)
        return None
    finally:
        if close:
            client.close()


def verify_textbook(path: Path) -> Tuple[bool, str]:
    """Валидация: открывается ли, есть ли структура (§, главы, оглавление)."""
    from .knowledge import parse_document

    try:
        text = parse_document(path)
    except Exception as e:
        return False, f"Файл не открывается: {e}"
    if len(text.strip()) < 50:
        return False, "Слишком мало текста"
    has_structure = bool(re.search(r"(?:Параграф|§|Глава|Раздел)", text, re.IGNORECASE))
    return True, "структура есть" if has_structure else "структура не обнаружена (но текст есть)"


# ----------------------------------------------------------------------
# crawl4ai (lazy; dynamic rendering БЕЗ обхода защит, К-2)
# ----------------------------------------------------------------------
async def crawl_page_js(url: str, settings: Any = None) -> Dict[str, Any]:
    """Загрузка JS-рендеримой страницы через crawl4ai. Без обхода капчи/аутентификации.

    Возвращает {"ok": bool, "markdown": str, "error": str}. На страницах с капчей/
    авторизацией честно возвращает ok=False (К-2, 6.3).
    """
    s = settings or default_settings
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    except ImportError as e:
        raise RuntimeError(
            "crawl4ai не установлен. Выполните: pip install crawl4ai "
            "и python -m playwright install chromium (Слайс 5b)."
        ) from e
    block = is_url_blocked(url)
    if block:
        return {"ok": False, "markdown": "", "error": block}
    browser_config = BrowserConfig(
        headless=s.CRAWL4AI_PLAYWRIGHT_HEADLESS,
        verbose=False,
        user_agent=USER_AGENT,
    )
    run_config = CrawlerRunConfig(
        page_timeout=s.CRAWL4AI_PLAYWRIGHT_TIMEOUT_MS,
        check_robots_txt=s.CRAWL4AI_RESPECT_ROBOTS,
        verbose=False,
    )
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
    md = getattr(result, "markdown", "") or ""
    if not md:
        return {"ok": False, "markdown": "", "error": "Страница недоступна (возможно, капча/авторизация)"}
    return {"ok": True, "markdown": md, "error": ""}


async def crawl_textbook_catalog(
    query: str,
    grade: str = "",
    subject: str = "",
    settings: Any = None,
) -> List[str]:
    """Обход каталогов-агрегаторов ТОЛЬКО как источника ссылок (6.1, К-2).

    Извлекает ссылки на официальные ресурсы из поисковой выдачи DDGS + crawl4ai.
    Возвращает список URL.
    """
    urls: List[str] = []
    query_full = f"{query} {subject} {grade} класс конспект урок".strip()
    for r in search_web(query_full, settings=settings or default_settings):
        urls.append(r.url)
    return urls


# ----------------------------------------------------------------------
# Fallback-цепочка сбора материалов (6.2)
# ----------------------------------------------------------------------
def collect_source_materials(
    subject: str,
    topic: str,
    grade: str = "",
    author: str = "",
    settings: Any = None,
    http: Optional[httpx.Client] = None,
    cache_dir: Optional[Path] = None,
    *,
    use_cache: bool = True,
    student_id: str = "",
    on_sources_collected: Optional[Callable[[List[Dict[str, Any]], Optional[str]], None]] = None,
) -> SourceCollection:
    """Основная fallback-цепочка (6.2): локальные PDF → веб-материалы по теме.

    1. Локальные PDF-учебники (Plan B/Plan A) — не требует авто-поиска (В-2).
    2. Поиск страниц-конспектов по теме (search_web) + fetch_url (Plan A).
    3. Ничего → status="failed" (узел source_failed, В-3).
    
    Кэширование (6.3): если use_cache=True и материалы уже есть в кэше по ключу
    "subject::topic::grade" — возвращается SourceCollection из кэша без поиска.
    При успешном поисходе результаты сохраняются в кэш. Если передан student_id,
    ключ включает его — кэш персональный по ученику.
    """
    s = settings or default_settings
    cache_dir = cache_dir or Path(getattr(s, 'SOURCES_CACHE_DIR', 'data'))
    sources: List[Dict[str, Any]] = []
    texts: List[str] = []
    
    # Ключ кэша: [student_id::]subject::topic::grade
    _sid = f"{student_id}::" if student_id else ""
    cache_key = f"{_sid}{subject}::{topic}::{grade}" if (topic or subject) else ""

    # 0) Проверка кэша материалов (если use_cache=True и key не пустой)
    if use_cache and cache_key:
        cached = _get_cached_materials(cache_key, cache_dir)
        if cached is not None:
            sources = cached["sources"]
            collection_id = cached.get("collection_id")
            return SourceCollection(
                status="ready",
                sources=sources,
                message=f"Кэшированные материалы по теме: {len(sources)} источников (collection={collection_id})",
            )

    # 1) Локальные учебники
    local = find_local_textbooks(s, subject=subject, author=author, grade=grade)
    if local:
        sources = [{"type": "local_pdf", "path": str(p), "license": "local" } for p in local]
        return SourceCollection(status="ready", sources=sources, message=f"Найден локальный учебник: {local[0].name}")

    # 2) Веб-материалы по теме (Plan A)
    close = False
    if http is None:
        http = httpx.Client(timeout=s.REQUEST_TIMEOUT)
        close = True
    try:
        # Для школьников добавляем «класс», для студентов — «конспект лекции/курс»
        grade_part = f"{grade} класс" if grade else ""
        audience_part = "" if grade else "лекция конспект курс"
        query = f"{topic or subject} {subject} {grade_part} {audience_part}".strip()
        query = re.sub(r"\s+", " ", query).strip()
        results = search_web(query, settings=s)
        if not results:
            return SourceCollection(
                status="failed",
                message="Материалы по теме не найдены",
                failed_reason="empty_result",
            )
        for r in results:
            allowed, reason = license_check(r.url, for_download=False)
            if not allowed:
                logger.info("Источник %s: %s (ссылочный, пропускаем)", r.url, reason)
                continue
            cached = _cache_read(r.url, cache_dir)
            if cached is not None:
                texts.append(cached)
                sources.append({"type": "page", "url": r.url, "license": reason, "cached": True})
                continue
            text, status = fetch_url(r.url, client=http)
            if status == "OK" and text:
                texts.append(text)
                sources.append({"type": "page", "url": r.url, "license": reason})
                _cache_write(r.url, cache_dir, text)
            time.sleep(s.CRAWL_RATE_LIMIT_SEC)
            if len(sources) >= s.MAX_CRAWL_PAGES:
                break
    finally:
        if close:
            http.close()

    if texts:
        # Сохраняем в кэш (если use_cache и key не пустой)
        if use_cache and cache_key:
            _set_cached_materials(cache_key, sources, collection_id=None, cache_dir=cache_dir)
        return SourceCollection(status="ready", sources=sources, texts=texts,
                                message=f"Собрано материалов по теме: {len(sources)} источников")
    return SourceCollection(status="failed", sources=sources,
                            message="Материалы по теме не найдены (лицензионно недоступно)",
                            failed_reason="license_blocked")


# ----------------------------------------------------------------------
# Кэш материалов по (subject, topic, grade) (6.3)
# ----------------------------------------------------------------------
_MATERIAL_CACHE_FILE_NAME = "material_cache.json"
_lock = threading.Lock()


def _material_cache_path(cache_dir: Optional[Path] = None) -> Path:
    """Путь к файлу кэша материалов."""
    from .config import settings as _settings
    base = cache_dir or Path(getattr(_settings, 'SOURCES_CACHE_DIR', 'data'))
    base.mkdir(parents=True, exist_ok=True)
    return base / _MATERIAL_CACHE_FILE_NAME


def _load_material_cache(cache_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Загрузить кэш материалов из JSON-файла."""
    path = _material_cache_path(cache_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_material_cache(data: Dict[str, Any], cache_dir: Optional[Path] = None) -> None:
    """Сохранить кэш материалов в JSON-файл."""
    path = _material_cache_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_cached_materials(key: str, cache_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Получить кэшированные материалы по ключу (subject::topic::grade).

    Возвращает dict с ключами 'sources' и 'collection_id' или None.
    """
    with _lock:
        cache = _load_material_cache(cache_dir)
        entry = cache.get(key)
        if entry and isinstance(entry, dict) and entry.get("sources"):
            logger.info("Материалы найдены в кэше: %s (%d источников)", key, len(entry.get("sources", [])))
            return entry
    return None


def _set_cached_materials(key: str, sources: List[Dict[str, Any]], collection_id: Optional[str] = None,
                          cache_dir: Optional[Path] = None) -> None:
    """Сохранить материалы в кэш по ключу (subject::topic::grade)."""
    with _lock:
        cache = _load_material_cache(cache_dir)
        cache[key] = {
            "sources": sources,
            "collection_id": collection_id,
            "cached_at": time.time(),
        }
        _save_material_cache(cache, cache_dir)
        logger.info("Материалы сохранены в кэш: %s (%d источников)", key, len(sources))
