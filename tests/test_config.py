"""Тесты конфигурации EduTutor (Слайс 0).

Юнит-тесты чистой логики (каскад провайдеров, поисковики, пути) — изолированы от
реального .env (фикстура make_settings чистит os.environ). Интеграционные тесты
загрузки реального .env — в TestRealEnv.
"""

from __future__ import annotations

import pytest

from src import config
from src.config import BASE_DIR, Settings


@pytest.fixture
def make_settings(monkeypatch):
    """Конструктор Settings, изолированный от переменных окружения/.env."""

    def _make(**kwargs):
        for name in Settings.model_fields:
            if name not in kwargs:
                monkeypatch.delenv(name, raising=False)
        return Settings(_env_file=None, **kwargs)

    return _make


class TestDefaults:
    def test_role_models_defaults(self, make_settings):
        s = make_settings()
        assert s.TUTOR_MODEL == "qwen/qwen3.7-flash"
        assert s.EXPERT_MODEL == "deepseek/deepseek-v4-flash"
        assert s.JUDGE_MODEL == "google/gemini-3.5-flash-lite"
        assert s.CHEAP_MODEL == "google/gemma-3-12b-it"
        assert s.EMBEDDING_MODEL == "intfloat/multilingual-e5-small"

    def test_limit_defaults(self, make_settings):
        s = make_settings()
        assert s.MAX_INTAKE_ITERATIONS == 8
        assert s.MAX_QUESTIONS_PER_SESSION == 15
        assert s.MAX_LLM_CALLS_PER_SESSION == 90
        assert s.MAX_COST_USD == 1.0
        assert s.CHEAP_ALLOWANCE_USD == 0.3
        assert s.TUTOR_ALLOWANCE_USD == 0.5
        assert s.JUDGE_ALLOWANCE_USD == 0.5
        assert s.SESSION_TIME_BUDGET_SEC == 900.0

    def test_crawl_defaults(self, make_settings):
        s = make_settings()
        assert s.CRAWL4AI_RESPECT_ROBOTS is True
        assert s.MAX_CRAWL_PAGES == 20
        assert s.MAX_TEXTBOOK_SEARCH_SEC == 300.0
        assert s.textbook_catalog_list == ["lesson.edu.ru", "ru.wikibooks.org", "resh.edu.ru", "rusneb.ru"]

    def test_relative_paths_absolutized(self, make_settings):
        s = make_settings(TEXTBOOKS_DOWNLOADS_DIR="./downloads")
        assert s.TEXTBOOKS_DOWNLOADS_DIR.is_absolute()
        assert s.TEXTBOOKS_DOWNLOADS_DIR == (BASE_DIR / "downloads").resolve()


class TestProviderCascade:
    """Каскад по провайдерам (раздел 4.1)."""

    def test_single_primary(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k", OPENROUTER_API_KEY="")
        providers = s.tutor_providers
        assert len(providers) == 1
        assert providers[0]["name"] == "routerai"
        assert providers[0]["model"] == s.TUTOR_MODEL

    def test_fallback_chain_primary_first(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k", OPENROUTER_API_KEY="k2")
        providers = s.expert_providers
        assert [p["name"] for p in providers] == ["routerai", "openrouter"]
        assert all(p["model"] == s.EXPERT_MODEL for p in providers)

    def test_primary_unavailable_promotes_available(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="", OPENROUTER_API_KEY="k2")
        providers = s.tutor_providers
        assert [p["name"] for p in providers] == ["openrouter"]

    def test_judge_never_uses_openrouter(self, make_settings):
        """К-4: судья работает только на RouterAI (без VPN)."""
        s = make_settings(ROUTERAI_API_KEY="k", OPENROUTER_API_KEY="k2")
        providers = s.judge_providers
        assert [p["name"] for p in providers] == ["routerai"]
        assert providers[0]["model"] == s.JUDGE_MODEL

    def test_cheap_role_model(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k")
        providers = s.cheap_providers
        assert providers[0]["model"] == s.CHEAP_MODEL

    def test_routerai_has_larger_timeout(self, make_settings):
        s = make_settings(ROUTERAI_API_KEY="k", OPENROUTER_API_KEY="k2")
        providers = s.tutor_providers
        routerai = next(p for p in providers if p["name"] == "routerai")
        openrouter = next(p for p in providers if p["name"] == "openrouter")
        assert routerai["timeout"] > openrouter["timeout"]

    def test_unknown_role_raises(self, make_settings):
        s = make_settings()
        with pytest.raises(ValueError):
            s.provider_configs("bogus")


class TestSearchEngines:
    def test_ddgs_always_last(self, make_settings):
        s = make_settings()
        assert s.search_engines[-1] == "ddgs"

    def test_yandex_when_configured(self, make_settings):
        s = make_settings(YANDEX_API_KEY="k", YANDEX_FOLDER_ID="f", SEARCH_PRIMARY="yandex")
        assert s.search_engines == ["yandex", "stepik", "ddgs"]

    def test_tavily_between(self, make_settings):
        s = make_settings(
            YANDEX_API_KEY="k", YANDEX_FOLDER_ID="f", TAVILY_API_KEY="t",
            SEARCH_PRIMARY="yandex",
        )
        assert s.search_engines == ["yandex", "tavily", "stepik", "ddgs"]

    def test_primary_not_configured_promotes_available(self, make_settings):
        s = make_settings(SEARCH_PRIMARY="tavily", YANDEX_API_KEY="k", YANDEX_FOLDER_ID="f")
        assert s.search_engines == ["yandex", "stepik", "ddgs"]

    def test_lesson_edu_opt_in(self, make_settings):
        s = make_settings()
        assert "lesson_edu" not in s.search_engines
        s2 = make_settings(ENABLE_LESSON_EDU=True)
        assert "lesson_edu" in s2.search_engines
        assert s2.search_engines[-1] == "ddgs"


class TestModelLists:
    def test_fallback_models_parsed(self, make_settings):
        s = make_settings(FALLBACK_MODELS="deepseek/deepseek-v4-flash-0731, qwen/qwen3.7-flash")
        assert s.fallback_models == ["deepseek/deepseek-v4-flash-0731", "qwen/qwen3.7-flash"]

    def test_judge_fallback_models_parsed(self, make_settings):
        s = make_settings(JUDGE_FALLBACK_MODELS="google/gemini-3.1-flash-lite, google/gemini-3.5-flash-lite")
        assert len(s.judge_fallback_models) == 2


class TestRealEnv:
    """Интеграционный тест: реальный .env (если он существует)."""

    @pytest.mark.skipif(not (BASE_DIR / ".env").exists(), reason="Нет .env")
    def test_real_env_loads_routerai(self):
        s = Settings()
        assert s.ROUTERAI_API_KEY, "ROUTERAI_API_KEY должен быть заполнен в .env"
        assert s.tutor_providers[0]["name"] == "routerai"

    @pytest.mark.skipif(not (BASE_DIR / ".env").exists(), reason="Нет .env")
    def test_real_env_judge_on_routerai(self):
        s = Settings()
        assert s.judge_providers[0]["name"] == "routerai"
