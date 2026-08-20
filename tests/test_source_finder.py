"""Тесты source_finder (Слайс 5): SSRF, license_check, search_web, скачивание, fallback-цепочка."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import sys
import types

from src.config import Settings
from src.source_finder import (
    SearchResult,
    collect_source_materials,
    crawl_page_js,
    download_file,
    fetch_html,
    fetch_url,
    find_local_textbooks,
    is_url_blocked,
    license_check,
    search_web,
    verify_textbook,
)


@pytest.fixture
def make_settings(monkeypatch):
    def _make(**kwargs):
        for name in Settings.model_fields:
            if name not in kwargs:
                monkeypatch.delenv(name, raising=False)
        return Settings(_env_file=None, **kwargs)

    return _make


class TestIsUrlBlocked:
    def test_localhost_blocked(self):
        assert is_url_blocked("http://localhost:8000/x") is not None
        assert is_url_blocked("http://127.0.0.1/admin") is not None

    def test_private_ip_blocked(self):
        assert is_url_blocked("http://192.168.1.1/") is not None
        assert is_url_blocked("http://10.0.0.5/") is not None
        assert is_url_blocked("http://172.16.0.1/") is not None

    def test_numeric_form_blocked(self):
        assert is_url_blocked("http://2130706433/") is not None
        assert is_url_blocked("http://0x7f000001/") is not None
        assert is_url_blocked("http://127.1/") is not None

    def test_metadata_blocked(self):
        assert is_url_blocked("http://169.254.169.254/latest/meta-data") is not None

    def test_public_host_allowed(self):
        assert is_url_blocked("https://ru.wikibooks.org/wiki/География") is None


class TestLicenseCheck:
    def test_download_allowed_host(self):
        ok, reason = license_check("https://ru.wikibooks.org/wiki/X.pdf", for_download=True)
        assert ok is True

    def test_scraper_blocked_for_download(self):
        ok, _ = license_check("https://11klassov.net/book.pdf", for_download=True)
        assert ok is False

    def test_scraper_is_link_only(self):
        ok, _ = license_check("https://11klassov.net/book", for_download=False)
        assert ok is False  # контент с «склада» не скачиваем даже как страницу

    def test_unknown_host_not_downloadable(self):
        ok, _ = license_check("https://example.com/book.pdf", for_download=True)
        assert ok is False
        ok2, _ = license_check("https://example.com/page", for_download=False)
        assert ok2 is True

    def test_bad_url(self):
        ok, _ = license_check("not a url", for_download=True)
        assert ok is False


class TestSearchWeb:
    def test_cascade_first_engine(self, make_settings):
        s = make_settings()

        def eng1(q, settings):
            return [SearchResult(title="a", url="https://a.ru")]

        def eng2(q, settings):
            raise AssertionError("не должен вызываться")

        engines = {"ddgs": eng1, "yandex": eng2}
        res = search_web("query", engines=engines, settings=s)
        assert len(res) == 1
        assert res[0].url == "https://a.ru"

    def test_cascade_fallback(self, make_settings):
        s = make_settings(YANDEX_API_KEY="k", YANDEX_FOLDER_ID="f", SEARCH_PRIMARY="yandex")

        def eng1(q, settings):
            raise RuntimeError("down")

        def eng2(q, settings):
            return [SearchResult(title="b", url="https://b.ru")]

        res = search_web("query", engines={"yandex": eng1, "ddgs": eng2}, settings=s)
        assert res[0].url == "https://b.ru"

    def test_all_fail_empty(self, make_settings):
        s = make_settings()

        def eng(q, settings):
            raise RuntimeError("down")

        assert search_web("q", engines={"ddgs": eng}, settings=s) == []


class TestFetchUrl:
    def _client_with(self, body: bytes, status: int = 200) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, content=body)

        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_returns_cleaned_text(self):
        body = "<html><body><p>Атмосфера &mdash; оболочка.</p></body></html>".encode("utf-8")
        client = self._client_with(body)
        text, status = fetch_url("https://ru.wikibooks.org/x", client=client)
        assert status == "OK"
        assert "Атмосфера" in text
        assert "<p>" not in text

    def test_preserves_structure_headings(self):
        """Веб-конспект: заголовки h1-h3 сохраняются как markdown-строки (не одна строка)."""
        from src.source_finder import _strip_html

        html = (
            "<html><body><h1>Кант</h1><p>Введение.</p>"
            "<h2>Биография</h2><p>Текст.</p><h3>Критика</h3><p>Текст.</p></body></html>"
        )
        text = _strip_html(html)
        assert "# Кант" in text
        assert "## Биография" in text
        assert "### Критика" in text
        # структура сохранилась: несколько строк, а не одна гигантская
        assert text.count("\n") >= 3

    def test_truncated(self, make_settings):
        s = make_settings(MAX_FETCH_CHARS=50)
        client = self._client_with(b"x " * 500)
        from src import source_finder

        source_finder.default_settings = s
        text, status = fetch_url("https://x.ru", client=client)
        assert status == "OK"
        assert len(text) <= 62  # 50 + «…[обрезано]»
        source_finder.default_settings = __import__("src.config", fromlist=["settings"]).settings

    def test_redirect_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/a":
                return httpx.Response(302, headers={"location": "/b"})
            return httpx.Response(200, content=b"<p>final</p>")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        text, status = fetch_url("https://x.ru/a", client=client)
        assert status == "OK"
        assert "final" in text

    def test_ssrf_blocked_before_request(self):
        client = self._client_with(b"<p>x</p>")
        text, status = fetch_url("http://127.0.0.1/x", client=client)
        assert status == "ERROR"
        assert "заблокирован" in text

    def test_http_error(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        text, status = fetch_url("https://x.ru/404", client=client)
        assert status == "ERROR"


class TestFetchHtml:
    def test_returns_raw_html(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<a href='/x'>link</a>")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        html, status = fetch_html("https://x.ru", client=client)
        assert status == "OK"
        assert "<a href='/x'>" in html


class TestFindLocalTextbooks:
    def test_finds_by_subject(self, tmp_path: Path, make_settings):
        (tmp_path / "_56_klassy_alekseev_a_i_2024.pdf").write_bytes(b"%PDF-1.1")
        s = make_settings(TEXTBOOKS_DOWNLOADS_DIR=str(tmp_path))
        found = find_local_textbooks(s, subject="география")
        assert len(found) == 1
        assert "alekseev" in found[0].stem.lower()

    def test_empty_dir(self, tmp_path: Path, make_settings):
        s = make_settings(TEXTBOOKS_DOWNLOADS_DIR=str(tmp_path))
        assert find_local_textbooks(s) == []


class TestDownloadFile:
    def _client_returning(self, data: bytes) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data)))

    def test_downloads_pdf(self, tmp_path: Path):
        client = self._client_returning(b"%PDF-1.4 fake pdf content")
        dest = tmp_path / "out.pdf"
        result = download_file("https://ru.wikibooks.org/x.pdf", dest, client=client)
        assert result == dest
        assert dest.read_bytes().startswith(b"%PDF")

    def test_rejects_non_pdf(self, tmp_path: Path):
        client = self._client_returning(b"<html>not a pdf</html>")
        dest = tmp_path / "out.pdf"
        assert download_file("https://ru.wikibooks.org/x.pdf", dest, client=client) is None
        assert not dest.exists()

    def test_license_rejects(self, tmp_path: Path):
        client = self._client_returning(b"%PDF-1.4")
        dest = tmp_path / "out.pdf"
        assert download_file("https://11klassov.net/x.pdf", dest, client=client) is None

    def test_ssrf_rejects(self, tmp_path: Path):
        client = self._client_returning(b"%PDF-1.4")
        dest = tmp_path / "out.pdf"
        assert download_file("http://127.0.0.1/x.pdf", dest, client=client) is None


class TestVerifyTextbook:
    def test_ok_with_structure(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("Параграф 12: Атмосфера\nСтроение атмосферы.\n" * 5, encoding="utf-8")
        ok, reason = verify_textbook(f)
        assert ok is True

    def test_fails_on_garbage(self, tmp_path: Path):
        f = tmp_path / "bad.txt"
        f.write_text("ab", encoding="utf-8")
        ok, _ = verify_textbook(f)
        assert ok is False


class TestCrawlPageJs:
    _MARKDOWN = "# Атмосфера\nСтроение атмосферы."

    def _install_fake_crawl4ai(self, monkeypatch, markdown):
        fake = types.ModuleType("crawl4ai")

        class FakeResult:
            def __init__(self):
                self.markdown = markdown

        class FakeCrawler:
            def __init__(self, config=None, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def arun(self, url, config=None, **kw):
                return FakeResult()

        class FakeBrowserConfig:
            def __init__(self, **kw):
                pass

        class FakeRunConfig:
            def __init__(self, **kw):
                pass

        fake.AsyncWebCrawler = FakeCrawler
        fake.BrowserConfig = FakeBrowserConfig
        fake.CrawlerRunConfig = FakeRunConfig
        monkeypatch.setitem(sys.modules, "crawl4ai", fake)

    async def test_ok(self, monkeypatch):
        self._install_fake_crawl4ai(monkeypatch, self._MARKDOWN)
        res = await crawl_page_js("https://ru.wikibooks.org/wiki/География")
        assert res["ok"] is True
        assert "Атмосфера" in res["markdown"]

    async def test_empty_markdown_fails(self, monkeypatch):
        self._install_fake_crawl4ai(monkeypatch, "")
        res = await crawl_page_js("https://ru.wikibooks.org/wiki/География")
        assert res["ok"] is False

    async def test_ssrf_blocked(self, monkeypatch):
        self._install_fake_crawl4ai(monkeypatch, self._MARKDOWN)
        res = await crawl_page_js("http://127.0.0.1/x")
        assert res["ok"] is False
        assert "заблокирован" in res["error"]

    async def test_missing_package_raises(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "crawl4ai", raising=False)
        import builtins

        original = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "crawl4ai":
                raise ImportError("no crawl4ai")
            return original(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(RuntimeError) as exc:
            await crawl_page_js("https://ru.wikibooks.org/wiki/x")
        assert "crawl4ai" in str(exc.value)


class TestCrawlPageJsIntegration:
    """Интеграционный тест: реальный crawl4ai на открытой странице (wikibooks)."""

    @pytest.mark.skipif(
        not __import__("importlib.util").util.find_spec("crawl4ai"),
        reason="crawl4ai не установлен",
    )
    async def test_real_crawl_wikibooks(self):
        res = await crawl_page_js("https://ru.wikibooks.org/wiki/География")
        assert res["ok"] is True
        assert len(res["markdown"]) > 500


class TestCollectSourceMaterials:
    def test_local_pdf_first(self, tmp_path: Path, make_settings, monkeypatch):
        (tmp_path / "alekseev_geografia.pdf").write_bytes(b"%PDF-1.1")
        s = make_settings(TEXTBOOKS_DOWNLOADS_DIR=str(tmp_path))
        col = collect_source_materials("география", "Атмосфера", author="Алексеев", settings=s)
        assert col.status == "ready"
        assert col.sources[0]["type"] == "local_pdf"

    def test_web_fallback_ready(self, make_settings, monkeypatch):
        s = make_settings()
        monkeypatch.setattr(
            "src.source_finder.search_web",
            lambda q, settings=None: [SearchResult(title="t", url="https://ru.wikibooks.org/wiki/x")],
        )
        monkeypatch.setattr(
            "src.source_finder.fetch_url",
            lambda url, client=None: ("Текст конспекта про атмосферу.", "OK"),
        )
        col = collect_source_materials("география", "Атмосфера", settings=s)
        assert col.status == "ready"
        assert any("атмосфер" in t.lower() for t in col.texts)

    def test_empty_result_failed(self, make_settings, monkeypatch):
        s = make_settings()
        monkeypatch.setattr("src.source_finder.search_web", lambda q, settings=None: [])
        col = collect_source_materials("физика", "кванты", settings=s)
        assert col.status == "failed"
        assert col.failed_reason == "empty_result"

    def test_license_blocked_failed(self, make_settings, monkeypatch):
        s = make_settings()
        monkeypatch.setattr(
            "src.source_finder.search_web",
            lambda q, settings=None: [SearchResult(title="t", url="https://11klassov.net/x")],
        )
        col = collect_source_materials("физика", "кванты", settings=s)
        assert col.status == "failed"
        assert col.failed_reason == "license_blocked"
